#!/usr/bin/env bash
# deploy.sh — Deploy nomothetic to the Raspberry Pi over SSH.
#
# Usage:
#   ./scripts/deploy.sh [--local] [<version>] [<pi-host>]
#
# Arguments:
#   --local   Deploy the current local source tree (synced via rsync).
#             Bypasses git fetch/checkout on the Pi. Version is read from
#             pyproject.toml. Ignored if a version argument is also given.
#   version   Git tag to deploy (e.g. "v0.2.0"). If omitted, the script finds
#             and deploys the latest semver tag on the remote. Ignored if --local.
#   pi-host   SSH host (user@host or plain hostname). Overrides NOMON_PI_HOST.
#             If omitted and NOMON_PI_HOST is unset, runs locally — useful
#             when already connected to the Pi via SSH.
#
# Examples:
#   # Deploy local code from a dev machine to the Pi over SSH:
#   ./scripts/deploy.sh --local perceptua@perceptua
#
#   # Deploy latest release from a dev machine to the Pi over SSH:
#   ./scripts/deploy.sh perceptua@perceptua
#
#   # Deploy a specific version from a dev machine to the Pi over SSH:
#   ./scripts/deploy.sh v0.2.0 perceptua@perceptua
#
#   # Deploy local code directly on the Pi (no SSH needed):
#   ./scripts/deploy.sh --local
#
# Environment (read from .env in the repo root):
#   NOMON_PI_HOST     SSH target — "user@host" or plain hostname. Optional;
#                     if unset the script runs locally.
#   NOMON_SSH_KEY     Path to SSH private key (optional; if set it is passed to
#                     ssh with -i. If unset, SSH may prompt for a password or
#                     use the ssh-agent / default identity.)
#   NOMON_REMOTE_DIR  Absolute path to the repo directory on the Pi. Optional;
#                     defaults to ${HOME}/perceptua-nomon/nomothetic.
#
# The script (release mode) connects to the Pi and performs the following steps there:
#   1. Stops all nomothetic servers, including systemd-managed services if present.
#   2. Records the current git ref so it can be restored on failure.
#   3. Fetches tags from origin and checks out the target version.
#   4. Installs Python dependencies (production + dev extras).
#   5. Runs release checks: unit tests only.
#   6. Starts the API server, waits for readiness, starts the stream via the API,
#      performs a health check, then stops the stream and API.
#   7. Installs/updates systemd unit files and restarts the nomothetic services.
#
# The script (--local mode):
#   1. Reads the version from pyproject.toml.
#   2. Syncs the local source tree to the Pi via rsync (skipped if already on Pi).
#   3. Connects to the Pi and performs steps 1, 4–7 above (skipping git operations).
#
# Rollback:
#   If any step from 3–6 fails the script checks out the previous ref,
#   reinstalls production deps, and restarts the previously running services before exiting.
#
# Exit codes:
#   0  Deploy successful.
#   1  Usage / configuration error (no changes made on the Pi).
#   2  Deploy failed; rollback was performed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "${SCRIPT_DIR}")"

# ── Help ───────────────────────────────────────────────────────────────────────

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    sed -n '2,60p' "$0" | sed 's/^# \?//'
    exit 0
fi

# ── Load .env ──────────────────────────────────────────────────────────────────

ENV_FILE="${REPO_DIR}/.env"
if [[ -f "${ENV_FILE}" ]]; then
    while IFS= read -r line || [[ -n "${line}" ]]; do
        # Strip leading whitespace
        line="${line#"${line%%[![:space:]]*}"}"
        # Skip blank lines and comments
        [[ "${line}" =~ ^# || -z "${line}" ]] && continue
        key="${line%%=*}"
        val="${line#*=}"
        # Strip inline comment, surrounding whitespace, and optional quotes
        val="${val%%#*}"
        val="${val#"${val%%[![:space:]]*}"}"
        val="${val%"${val##*[![:space:]]}"}"
        val="${val#\"}" ; val="${val%\"}"
        val="${val#\'}" ; val="${val%\'}"
        case "${key}" in
            NOMON_PI_HOST|NOMON_SSH_KEY|NOMON_REMOTE_DIR) export "${key}=${val}" ;;
        esac
    done < "${ENV_FILE}"
fi

# ── Argument & configuration validation ───────────────────────────────────────

DEPLOY_LOCAL=false
VERSION="${1:-}"
PI_HOST="${2:-${NOMON_PI_HOST:-}}"

# Check if first argument is --local flag
if [[ "${VERSION}" == "--local" ]]; then
    DEPLOY_LOCAL=true
    VERSION=""
    PI_HOST="${2:-${NOMON_PI_HOST:-}}"
fi

if [[ -n "${VERSION}" && ! "${VERSION}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Error: version must start with 'v' followed by semver (e.g. v0.2.0)" >&2
    exit 1
fi

# ── Local mode: resolve version from pyproject.toml ───────────────────────────

if [[ "${DEPLOY_LOCAL}" == true ]]; then
    _raw_version="$(grep -m1 '^version' "${REPO_DIR}/pyproject.toml" \
        | sed -E 's/.*version\s*=\s*"([^"]+)".*/\1/')"
    if [[ -z "${_raw_version}" ]]; then
        echo "Error: could not determine version from pyproject.toml" >&2
        exit 1
    fi
    VERSION="v${_raw_version}"
    echo "==> Local deploy: nomothetic ${VERSION}"
fi

# ── SSH helpers ────────────────────────────────────────────────────────────────
# If PI_HOST is set we run everything remotely; otherwise we run locally.

if [[ -n "${PI_HOST}" ]]; then
    SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15)
    if [[ -n "${NOMON_SSH_KEY:-}" ]]; then
        SSH_OPTS+=(-i "${NOMON_SSH_KEY}")
    fi
    echo "==> Deploying nomothetic${VERSION:+ ${VERSION}} → ${PI_HOST}"
    RUN_CMD=(ssh "${SSH_OPTS[@]}" "${PI_HOST}" 'bash -ls "$@"' --)
else
    echo "==> Deploying nomothetic${VERSION:+ ${VERSION}} locally"
    RUN_CMD=(bash -ls --)
fi

# Deploy-only variables that must NOT be written to the on-device env file.
_DEPLOY_EXCLUDE='^\s*(NOMON_PI_HOST|NOMON_SSH_KEY|NOMON_REMOTE_DIR|NOMON_GITHUB_REPO)\s*='

copy_nomothetic_env() {
    if [[ ! -f "${ENV_FILE}" ]]; then
        echo "==> Warning: .env not found; skipping /etc/nomothetic/nomothetic.env creation." >&2
        return
    fi

    local filtered
    filtered="$(grep -vE "${_DEPLOY_EXCLUDE}" "${ENV_FILE}" \
        | grep -vE '^\s*#' \
        | grep -vE '^\s*$')"

    if [[ -n "${PI_HOST}" ]]; then
        echo "==> Writing /etc/nomothetic/nomothetic.env on remote host..."
        printf '%s\n' "${filtered}" \
            | ssh "${SSH_OPTS[@]}" "${PI_HOST}" \
                'sudo mkdir -p /etc/nomothetic && sudo tee /etc/nomothetic/nomothetic.env >/dev/null'
    else
        echo "==> Writing /etc/nomothetic/nomothetic.env locally..."
        sudo mkdir -p /etc/nomothetic
        printf '%s\n' "${filtered}" | sudo tee /etc/nomothetic/nomothetic.env > /dev/null
    fi
}

# ── Local mode: sync source tree to Pi ────────────────────────────────────────

if [[ "${DEPLOY_LOCAL}" == true && -n "${PI_HOST}" ]]; then
    _remote_dir="${NOMON_REMOTE_DIR:-}"
    # We can't expand $HOME for the remote side here, so default to a literal path
    # the remote script will also accept. Use a placeholder that ssh can resolve.
    _rsync_dest="${PI_HOST}:${_remote_dir:-~/perceptua-nomon/nomothetic/}"
    RSYNC_OPTS=(--archive --compress --delete
        --exclude='.git/'
        --exclude='__pycache__/'
        --exclude='*.pyc'
        --exclude='.venv/'
        --exclude='htmlcov/'
        --exclude='logs/'
    )
    if [[ -n "${NOMON_SSH_KEY:-}" ]]; then
        RSYNC_OPTS+=(-e "ssh -i ${NOMON_SSH_KEY} -o StrictHostKeyChecking=accept-new")
    else
        RSYNC_OPTS+=(-e "ssh -o StrictHostKeyChecking=accept-new")
    fi
    echo "==> Syncing local source → ${_rsync_dest}..."
    rsync "${RSYNC_OPTS[@]}" "${REPO_DIR}/" "${_rsync_dest}"
    echo "  Sync complete ✓"
fi

copy_nomothetic_env

# ── Deployment ─────────────────────────────────────────────────────────────────
# All steps below run on the Pi (remote or local) via a single shell session.

"${RUN_CMD[@]}" "${VERSION}" "${DEPLOY_LOCAL}" "${NOMON_REMOTE_DIR:-}" << 'END_REMOTE'
set -euo pipefail

readonly REQUESTED_VERSION="$1"
readonly DEPLOY_LOCAL="${2:-false}"
readonly REMOTE_DIR="${3:-${HOME}/perceptua-nomon/nomothetic}"

if [[ ! -d "${REMOTE_DIR}" ]]; then
    echo "Error: ${REMOTE_DIR} does not exist on the Pi." >&2
    exit 1
fi

cd "${REMOTE_DIR}"

# ── Save current ref for rollback (release mode only) ─────────────────────────

if [[ "${DEPLOY_LOCAL}" != "true" ]]; then
    PREV_REF="$(git rev-parse HEAD)"
    PREV_LABEL="$(git describe --tags --exact-match HEAD 2>/dev/null \
                  || git rev-parse --short HEAD)"
    echo "  Current ref: ${PREV_LABEL}"
fi

# ── Resolve target version (pre-flight, before we touch anything) ─────────────

if [[ "${DEPLOY_LOCAL}" == "true" ]]; then
    # Version was already resolved from pyproject.toml on the dev machine.
    TARGET="${REQUESTED_VERSION}"
    echo "==> Target: ${TARGET} (local source)"
else
    echo "==> Fetching tags from origin..."
    git fetch --tags --quiet

    TARGET="${REQUESTED_VERSION}"
    if [[ -z "${TARGET}" ]]; then
        TARGET="$(git tag --list 'v*' --sort=-version:refname | head -1)"
        if [[ -z "${TARGET}" ]]; then
            echo "Error: no semver tags found in the repository." >&2
            exit 1
        fi
        echo "  Latest release tag: ${TARGET}"
    fi

    if [[ ! "${TARGET}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "Error: resolved tag '${TARGET}' is not a valid semver tag." >&2
        exit 1
    fi

    CURRENT_TAG="$(git describe --tags --exact-match HEAD 2>/dev/null || true)"
    if [[ "${CURRENT_TAG}" == "${TARGET}" ]]; then
        echo "  Note: already on ${TARGET}; re-running checks and restarting servers."
    fi

    echo "==> Target: ${TARGET}"
fi

# ── Rollback helper ────────────────────────────────────────────────────────────
# Set up only after pre-flight so that early errors (tag resolution etc.) do
# not trigger a rollback — nothing has been changed on disk at that point.

_ROLLING_BACK=0

rollback() {
    # Guard against re-entry (e.g. if reinstall in rollback also fails).
    [[ "${_ROLLING_BACK}" -eq 1 ]] && exit 2
    _ROLLING_BACK=1

    echo "" >&2
    echo "!! Deployment failed. Rolling back to ${PREV_LABEL:-local}..." >&2

    if [[ "${DEPLOY_LOCAL}" != "true" ]]; then
        git checkout --quiet "${PREV_REF}" || true
    fi

    echo "  Reinstalling previous version..." >&2
    uv sync --extra pi --extra web --extra api --extra telemetry 2>&1 || true

    if [[ "${SYSTEMD_AVAILABLE}" == "true" ]]; then
        if [[ "${PREV_API_SERVICE_ACTIVE}" == "true" ]]; then
            echo "  Restarting nomothetic-api.service..." >&2
            sudo systemctl restart nomothetic-api.service 2>&1 || true
        fi
        if [[ "${PREV_STREAM_SERVICE_ACTIVE}" == "true" ]]; then
            echo "  Restarting nomothetic-stream.service..." >&2
            sudo systemctl restart nomothetic-stream.service 2>&1 || true
        fi
    else
        echo "  Restarting API server..." >&2
        NOMON_API_MODE=device NOMON_DEVICE_AUTH=false ./scripts/start.sh api 2>&1 || true
    fi

    echo "!! Rollback complete. Services restored to ${PREV_LABEL:-local}." >&2
    exit 2
}

trap rollback ERR

# ── Systemd service state capture ───────────────────────────────────────────────

SYSTEMD_AVAILABLE=false
PREV_API_SERVICE_ACTIVE=false
PREV_STREAM_SERVICE_ACTIVE=false

if command -v systemctl >/dev/null 2>&1; then
    SYSTEMD_AVAILABLE=true
    for _svc in nomothetic-api nomothetic-stream; do
        if sudo systemctl list-unit-files --full --no-legend "${_svc}.service" >/dev/null 2>&1; then
            if sudo systemctl is-active --quiet "${_svc}.service"; then
                if [[ "${_svc}" == "nomothetic-api" ]]; then
                    PREV_API_SERVICE_ACTIVE=true
                else
                    PREV_STREAM_SERVICE_ACTIVE=true
                fi
            fi
            echo "  Stopping ${_svc}.service if it exists..."
            sudo systemctl stop "${_svc}.service" 2>/dev/null || true
        fi
    done
fi

echo "==> Stopping servers..."
./scripts/stop.sh all

# ── Checkout target version (release mode only) ────────────────────────────────

if [[ "${DEPLOY_LOCAL}" != "true" ]]; then
    echo "==> Checking out ${TARGET}..."
    git checkout --quiet "${TARGET}"
fi

# ── Install dependencies ───────────────────────────────────────────────────────

echo "==> Installing dependencies..."
make install-pi

# ── Release checks ─────────────────────────────────────────────────────────────

echo "==> Running tests..."
make test

# ── Start servers & verify liveness ───────────────────────────────────────────

# Derive the API base URL from config.toml so curl hits the right endpoint.
_api_cfg=$(python3 - config.toml <<'PYEOF'
import sys
try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]
with open(sys.argv[1], "rb") as f:
    cfg = tomllib.load(f)
a = cfg.get("api", {})
print("NOM_API_PORT=" + str(int(a.get("port", 8443))))
print("NOM_API_USE_SSL=" + str(bool(a.get("use_ssl", True))).lower())
PYEOF
)
eval "${_api_cfg}"
_scheme="$([[ "${NOM_API_USE_SSL}" == "true" ]] && echo "https" || echo "http")"
_api_base="${_scheme}://127.0.0.1:${NOM_API_PORT}"
_curl=(curl -sf -k --max-time 5)

echo "==> Starting API server..."
NOMON_API_MODE=device NOMON_DEVICE_AUTH=false ./scripts/start.sh api

echo "==> Waiting for API to be ready..."
_attempts=0
until "${_curl[@]}" "${_api_base}/" > /dev/null 2>&1; do
    _attempts=$(( _attempts + 1 ))
    if [[ "${_attempts}" -ge 12 ]]; then
        echo "Error: API server did not respond after 30 s." >&2
        exit 1
    fi
    sleep 2.5
done
echo "  API ready ✓"

echo "==> Starting stream server via API..."
_stream_resp="$(curl -sk --max-time 10 \
    -X POST "${_api_base}/api/stream/start" \
    -H "Content-Type: application/json" \
    -d '{}' \
    -w "\n%{http_code}")"
_stream_code="$(printf '%s' "${_stream_resp}" | tail -1)"
_stream_body="$(printf '%s' "${_stream_resp}" | sed '$d')"
if [[ "${_stream_code}" != "200" ]]; then
    echo "Error: failed to start stream server (HTTP ${_stream_code})" >&2
    echo "  Response: ${_stream_body}" >&2
    exit 1
fi
echo "  Stream server started ✓"

echo "==> Health check..."
_health="$("${_curl[@]}" "${_api_base}/")"
_status="$(printf '%s' "${_health}" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("status",""))')"
if [[ "${_status}" != "ok" ]]; then
    echo "Error: health check failed — response: ${_health}" >&2
    exit 1
fi
echo "  Health: ${_status} ✓"

echo "==> Stopping stream server via API..."
"${_curl[@]}" -X POST "${_api_base}/api/stream/stop" > /dev/null
echo "  Stream server stopped ✓"

echo "==> Stopping API server..."
./scripts/stop.sh api

# ── Systemd integration (optional) ────────────────────────────────────────────
# Install and enable systemd service files if systemd is available.

if command -v systemctl >/dev/null 2>&1; then
    _systemd_changed=false

    # Resolve service identity for template substitution.
    if ! command -v envsubst >/dev/null 2>&1; then
        echo "Error: envsubst not found. Install: sudo apt-get install -y gettext-base" >&2
        exit 1
    fi
    if [[ -f /etc/nomothetic/nomothetic.env ]]; then
        set -o allexport
        # shellcheck disable=SC1091
        source /etc/nomothetic/nomothetic.env
        set +o allexport
    fi
    export NOMON_SERVICE_USER="${NOMON_SERVICE_USER:-nomon}"
    export NOMON_SERVICE_GROUP="${NOMON_SERVICE_GROUP:-nomon}"

    for _svc_file in systemd/*.service; do
        [[ -f "${_svc_file}" ]] || continue
        _svc_name="$(basename "${_svc_file}")"
        _dest="/etc/systemd/system/${_svc_name}"

        _expanded="$(envsubst '$NOMON_SERVICE_USER $NOMON_SERVICE_GROUP' < "${_svc_file}")"
        if [[ ! -f "${_dest}" ]] || [[ "${_expanded}" != "$(cat "${_dest}")" ]]; then
            echo "  Installing ${_svc_name}..."
            printf '%s\n' "${_expanded}" | sudo tee "${_dest}" > /dev/null
            sudo chmod 644 "${_dest}"
            _systemd_changed=true
        fi
    done

    if [[ "${_systemd_changed}" == "true" ]]; then
        echo "  Reloading systemd daemon..."
        sudo systemctl daemon-reload
    fi

    # Enable and restart the device-mode services.
    for _svc in nomothetic-api nomothetic-stream; do
        if [[ -f "/etc/systemd/system/${_svc}.service" ]]; then
            sudo systemctl enable "${_svc}.service" 2>/dev/null || true
            echo "  Restarting ${_svc}..."
            sudo systemctl restart "${_svc}.service"
        fi
    done

    echo "  systemd services updated ✓"
else
    echo "  systemd not available — skipping service installation."
fi

echo ""
echo "✓ nomothetic ${TARGET} deployed successfully to ${HOSTNAME}."
END_REMOTE
