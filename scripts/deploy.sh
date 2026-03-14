#!/usr/bin/env bash
# deploy.sh — Deploy nomothetic to the Raspberry Pi over SSH.
#
# Usage:
#   ./scripts/deploy.sh [<version>] [<pi-host>]
#
# Arguments:
#   version   Git tag to deploy (e.g. "v0.2.0"). If omitted, the script finds
#             and deploys the latest semver tag on the remote.
#   pi-host   SSH host (user@host or plain hostname). Overrides NOMON_PI_HOST.
#             If omitted and NOMON_PI_HOST is unset, runs locally — useful
#             when already connected to the Pi via SSH.
#
# Examples:
#   # Deploy from a dev machine to the Pi over SSH:
#   ./scripts/deploy.sh v0.2.0 perceptua@perceptua
#
#   # Deploy directly on the Pi (no SSH needed):
#   ./scripts/deploy.sh v0.2.0
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
# The script connects to the Pi and performs the following steps there:
#   1. Stops all nomothetic servers.
#   2. Records the current git ref so it can be restored on failure.
#   3. Fetches tags from origin and checks out the target version.
#   4. Installs Python dependencies (production + dev extras).
#   5. Runs release checks: lint (ruff), format (black), type-check (mypy), tests.
#   6. Starts the API server, waits for readiness, starts the stream via the API,
#      performs a health check, then stops the stream and API.
#
# Rollback:
#   If any step from 3–6 fails the script checks out the previous ref,
#   reinstalls production deps, and restarts the servers before exiting.
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
    sed -n '2,30p' "$0" | sed 's/^# \?//'
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

VERSION="${1:-}"
PI_HOST="${2:-${NOMON_PI_HOST:-}}"

if [[ -n "${VERSION}" && ! "${VERSION}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Error: version must start with 'v' followed by semver (e.g. v0.2.0)" >&2
    exit 1
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

# ── Deployment ─────────────────────────────────────────────────────────────────
# All steps below run on the Pi (remote or local) via a single shell session.

"${RUN_CMD[@]}" "${VERSION}" "${NOMON_REMOTE_DIR:-}" << 'END_REMOTE'
set -euo pipefail

readonly REQUESTED_VERSION="$1"
readonly REMOTE_DIR="${2:-${HOME}/perceptua-nomon/nomothetic}"

if [[ ! -d "${REMOTE_DIR}" ]]; then
    echo "Error: ${REMOTE_DIR} does not exist on the Pi." >&2
    exit 1
fi

cd "${REMOTE_DIR}"

# ── Save current ref for rollback ─────────────────────────────────────────────

PREV_REF="$(git rev-parse HEAD)"
PREV_LABEL="$(git describe --tags --exact-match HEAD 2>/dev/null \
              || git rev-parse --short HEAD)"
echo "  Current ref: ${PREV_LABEL}"

# ── Resolve target version (pre-flight, before we touch anything) ─────────────

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

# ── Rollback helper ────────────────────────────────────────────────────────────
# Set up only after pre-flight so that early errors (tag resolution etc.) do
# not trigger a rollback — nothing has been changed on disk at that point.

_ROLLING_BACK=0

rollback() {
    # Guard against re-entry (e.g. if reinstall in rollback also fails).
    [[ "${_ROLLING_BACK}" -eq 1 ]] && exit 2
    _ROLLING_BACK=1

    echo "" >&2
    echo "!! Deployment failed. Rolling back to ${PREV_LABEL}..." >&2

    git checkout --quiet "${PREV_REF}" || true

    echo "  Reinstalling previous version..." >&2
    uv sync --extra pi --extra web --extra api --extra telemetry 2>&1 || true

    echo "  Restarting API server..." >&2
    ./scripts/start.sh api 2>&1 || true

    echo "!! Rollback complete. API server restored to ${PREV_LABEL}." >&2
    exit 2
}

trap rollback ERR

# ── Stop servers ───────────────────────────────────────────────────────────────

echo "==> Stopping servers..."
./scripts/stop.sh all

# ── Checkout target version ────────────────────────────────────────────────────

echo "==> Checking out ${TARGET}..."
git checkout --quiet "${TARGET}"

# ── Install dependencies ───────────────────────────────────────────────────────

echo "==> Installing dependencies..."
make install-pi

# ── Release checks ─────────────────────────────────────────────────────────────

echo "==> Release checks..."
make check

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
./scripts/start.sh api

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
"${_curl[@]}" -X POST "${_api_base}/api/stream/start" \
    -H "Content-Type: application/json" -d '{}' > /dev/null
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

echo ""
echo "✓ nomothetic ${TARGET} deployed successfully to ${HOSTNAME}."
END_REMOTE
