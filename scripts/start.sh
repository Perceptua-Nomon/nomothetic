#!/usr/bin/env bash
# Start a nomothetic server in the background.
#
# Usage:
#   ./scripts/start.sh <stream|api|all> [OPTIONS]
#
# Arguments:
#   stream   Start the MJPEG stream server (Flask, HTTP).
#   api      Start the REST API server (FastAPI/uvicorn, HTTPS).
#   all      Start both the stream and API servers.
#
# Options:
#   --config FILE   Path to TOML config file.
#                   Defaults to ./config.toml, then <repo-root>/config.toml.
#   --foreground    Run in the foreground instead of backgrounding (useful
#                   for debugging – Ctrl-C to stop). Not supported with 'all'.
#   -h, --help      Show this help and exit.
#
# The server PID is written to /tmp/nomothetic-<type>.pid.
# Stop it with:
#   ./scripts/stop.sh <stream|api|all>
# or:
#   kill $(cat /tmp/nomothetic-<type>.pid)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "${SCRIPT_DIR}")"

CONFIG_FILE=""
FOREGROUND=false

# ─── Parse server type (required first arg) ───────────────────────────────────
if [[ $# -eq 0 || "$1" == "-h" || "$1" == "--help" ]]; then
  sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

SERVER_TYPE="$1"
shift

if [[ "${SERVER_TYPE}" != "stream" && "${SERVER_TYPE}" != "api" && "${SERVER_TYPE}" != "all" ]]; then
  echo "Error: server type must be 'stream', 'api', or 'all', got '${SERVER_TYPE}'." >&2
  echo "Run '$(basename "$0") --help' for usage." >&2
  exit 1
fi

# ─── Parse optional arguments ─────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case $1 in
    --config)
      CONFIG_FILE="$2"
      shift 2
      ;;
    --foreground)
      FOREGROUND=true
      shift
      ;;
    -h|--help)
      sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Error: unknown argument: $1" >&2
      echo "Run '$(basename "$0") --help' for usage." >&2
      exit 1
      ;;
  esac
done

# ─── Handle 'all' by delegating to stream + api ──────────────────────────────
if [[ "${SERVER_TYPE}" == "all" ]]; then
  if [[ "${FOREGROUND}" == "true" ]]; then
    echo "Error: --foreground is not supported with 'all'; use 'stream' or 'api' directly." >&2
    exit 1
  fi
  FORWARD_ARGS=()
  [[ -n "${CONFIG_FILE}" ]] && FORWARD_ARGS+=(--config "${CONFIG_FILE}")
  "$0" stream "${FORWARD_ARGS[@]}"
  "$0" api    "${FORWARD_ARGS[@]}"
  exit 0
fi

PID_FILE="/tmp/nomothetic-${SERVER_TYPE}.pid"

# ─── Locate config file ───────────────────────────────────────────────────────
if [[ -z "${CONFIG_FILE}" ]]; then
  if [[ -f "${PWD}/config.toml" ]]; then
    CONFIG_FILE="${PWD}/config.toml"
  elif [[ -f "${REPO_DIR}/config.toml" ]]; then
    CONFIG_FILE="${REPO_DIR}/config.toml"
  else
    echo "Error: config.toml not found." >&2
    echo "  Create one from: ${REPO_DIR}/config.toml.example" >&2
    exit 1
  fi
fi

# ─── Activate virtual environment if present ─────────────────────────────────
VENV_ACTIVATE="${REPO_DIR}/.venv/bin/activate"
if [[ -f "${VENV_ACTIVATE}" ]]; then
  # shellcheck source=/dev/null
  source "${VENV_ACTIVATE}"
fi

# ─── Parse TOML config via Python ────────────────────────────────────────────
_parsed_cfg=$(python3 - "${CONFIG_FILE}" "${SERVER_TYPE}" <<'PYEOF'
import sys

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        sys.stderr.write(
            "Error: TOML support is missing.\n"
            "  Python 3.11+ includes tomllib, or install tomli:\n"
            "    pip install tomli\n"
        )
        sys.exit(1)

with open(sys.argv[1], "rb") as f:
    cfg = tomllib.load(f)

server_type = sys.argv[2]
lg = cfg.get("logging", {})
print("NOM_LOG_DIR=" + repr(str(lg.get("log_dir", "logs"))))

if server_type == "stream":
    s = cfg.get("stream", {})
    print("NOM_STREAM_HOST="    + repr(str(s.get("host",        "0.0.0.0"))))
    print("NOM_STREAM_PORT="    + str(int(s.get("port",         8000))))
    print("NOM_STREAM_CAMERA="  + str(int(s.get("camera_index", 0))))
    print("NOM_STREAM_WIDTH="   + str(int(s.get("width",        1280))))
    print("NOM_STREAM_HEIGHT="  + str(int(s.get("height",       720))))
    print("NOM_STREAM_FPS="     + str(int(s.get("fps",          30))))
    print("NOM_STREAM_ENCODER=" + repr(str(s.get("encoder",     "h264"))))
else:
    a = cfg.get("api", {})
    h = cfg.get("hat", {})
    print("NOM_API_HOST="     + repr(str(a.get("host",        "0.0.0.0"))))
    print("NOM_API_PORT="     + str(int(a.get("port",         8443))))
    print("NOM_API_USE_SSL="  + str(bool(a.get("use_ssl",     True))).lower())
    print("NOM_API_CERT_DIR=" + repr(str(a.get("cert_dir",    ".certs"))))
    print("NOM_HAT_SOCKET="   + repr(str(h.get("socket_path", ""))))
PYEOF
)
eval "${_parsed_cfg}"

# ─── Resolve log directory ────────────────────────────────────────────────────
if [[ "${NOM_LOG_DIR}" != /* ]]; then
  NOM_LOG_DIR="${REPO_DIR}/${NOM_LOG_DIR}"
fi
mkdir -p "${NOM_LOG_DIR}"
LOG_FILE="${NOM_LOG_DIR}/${SERVER_TYPE}.log"

# ─── Build server-specific launch snippet and display info ───────────────────
if [[ "${SERVER_TYPE}" == "stream" ]]; then
  export NOM_STREAM_HOST NOM_STREAM_PORT NOM_STREAM_CAMERA
  export NOM_STREAM_WIDTH NOM_STREAM_HEIGHT NOM_STREAM_FPS NOM_STREAM_ENCODER
  DISPLAY_URL="http://${NOM_STREAM_HOST}:${NOM_STREAM_PORT}"
  DISPLAY_EXTRA=""
  LAUNCH_PY="
import os
from nomothetic.streaming import StreamServer
StreamServer(
    host=os.environ['NOM_STREAM_HOST'],
    port=int(os.environ['NOM_STREAM_PORT']),
    camera_index=int(os.environ['NOM_STREAM_CAMERA']),
    width=int(os.environ['NOM_STREAM_WIDTH']),
    height=int(os.environ['NOM_STREAM_HEIGHT']),
    fps=int(os.environ['NOM_STREAM_FPS']),
    encoder=os.environ['NOM_STREAM_ENCODER'],
).start()
"
else
  if [[ -n "${NOM_HAT_SOCKET-}" ]]; then
    NOMON_HAT_SOCKET_PATH="${NOM_HAT_SOCKET}"
  fi
  export NOM_API_HOST NOM_API_PORT NOM_API_USE_SSL NOM_API_CERT_DIR NOMON_HAT_SOCKET_PATH
  SCHEME="$([[ "${NOM_API_USE_SSL}" == "true" ]] && echo "https" || echo "http")"
  DISPLAY_URL="${SCHEME}://${NOM_API_HOST}:${NOM_API_PORT}"
  DISPLAY_EXTRA="  Docs: ${DISPLAY_URL}/docs"$'\n'
  LAUNCH_PY="
import os
from nomothetic.api import APIServer
APIServer(
    host=os.environ['NOM_API_HOST'],
    port=int(os.environ['NOM_API_PORT']),
    use_ssl=(os.environ['NOM_API_USE_SSL'] == 'true'),
    cert_dir=os.environ['NOM_API_CERT_DIR'] or None,
).run()
"
fi

# ─── Foreground mode ─────────────────────────────────────────────────────────
if [[ "${FOREGROUND}" == "true" ]]; then
  echo "Starting ${SERVER_TYPE} server in the foreground (Ctrl-C to stop)..."
  echo "  URL:  ${DISPLAY_URL}"
  exec python3 -c "${LAUNCH_PY}"
fi

# ─── Check if already running ────────────────────────────────────────────────
if [[ -f "${PID_FILE}" ]]; then
  OLD_PID="$(cat "${PID_FILE}")"
  if kill -0 "${OLD_PID}" 2>/dev/null; then
    echo "${SERVER_TYPE} server is already running (PID ${OLD_PID})."
    exit 0
  fi
  rm -f "${PID_FILE}"
fi

# ─── Launch server in background ─────────────────────────────────────────────
nohup python3 -c "${LAUNCH_PY}" >> "${LOG_FILE}" 2>&1 &

SERVER_PID=$!
echo "${SERVER_PID}" > "${PID_FILE}"

echo "${SERVER_TYPE} server started."
echo "  PID:  ${SERVER_PID}  (${PID_FILE})"
echo "  URL:  ${DISPLAY_URL}"
printf '%s' "${DISPLAY_EXTRA}"
echo "  Logs: ${LOG_FILE}"
echo "  Stop: ./scripts/stop.sh ${SERVER_TYPE}"
