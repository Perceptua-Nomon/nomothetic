#!/usr/bin/env bash
# Stop a nomothetic background server.
#
# Usage:
#   ./scripts/stop.sh <stream|api|all>
#
# Arguments:
#   stream   Stop the MJPEG stream server.
#   api      Stop the REST API server.
#   all      Stop both servers.
#
# Options:
#   -h, --help  Show this help and exit.

set -euo pipefail

STREAM_PID_FILE="/tmp/nomothetic-stream.pid"
API_PID_FILE="/tmp/nomothetic-api.pid"

# ─── Parse server type (required first arg) ───────────────────────────────────
if [[ $# -eq 0 || "$1" == "-h" || "$1" == "--help" ]]; then
  sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

SERVER_TYPE="$1"
shift

if [[ "${SERVER_TYPE}" != "stream" && "${SERVER_TYPE}" != "api" && "${SERVER_TYPE}" != "all" ]]; then
  echo "Error: server type must be 'stream', 'api', or 'all', got '${SERVER_TYPE}'." >&2
  echo "Run '$(basename "$0") --help' for usage." >&2
  exit 1
fi

STOP_STREAM=false
STOP_API=false

[[ "${SERVER_TYPE}" == "stream" || "${SERVER_TYPE}" == "all" ]] && STOP_STREAM=true
[[ "${SERVER_TYPE}" == "api"    || "${SERVER_TYPE}" == "all" ]] && STOP_API=true

# ─── Helper: stop a server by PID file ───────────────────────────────────────
stop_server() {
  local name="$1"
  local pid_file="$2"

  if [[ ! -f "${pid_file}" ]]; then
    echo "${name}: not running (no PID file found)."
    return
  fi

  local pid
  pid="$(cat "${pid_file}")"

  if ! kill -0 "${pid}" 2>/dev/null; then
    echo "${name}: not running (stale PID ${pid} – cleaning up)."
    rm -f "${pid_file}"
    return
  fi

  kill "${pid}"
  # Wait briefly for the process to exit before removing the PID file
  local waited=0
  while kill -0 "${pid}" 2>/dev/null && [[ ${waited} -lt 10 ]]; do
    sleep 0.2
    waited=$(( waited + 1 ))
  done

  rm -f "${pid_file}"
  echo "${name}: stopped (PID ${pid})."
}

# ─── Stop requested servers ───────────────────────────────────────────────────
[[ "${STOP_STREAM}" == "true" ]] && stop_server "Stream server" "${STREAM_PID_FILE}"
[[ "${STOP_API}"    == "true" ]] && stop_server "API server"    "${API_PID_FILE}"
