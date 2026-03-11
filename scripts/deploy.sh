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
#
# Environment (read from .env in the repo root):
#   NOMON_PI_HOST   SSH target — "user@host" or plain hostname. Required.
#   NOMON_SSH_KEY   Path to SSH private key (optional; if set it is passed to
#                   ssh with -i. If unset, SSH may prompt for a password or
#                   use the ssh-agent / default identity.)
#
# The script connects to the Pi and performs the following steps there:
#   1. Stops all nomothetic servers.
#   2. Records the current git ref so it can be restored on failure.
#   3. Fetches tags from origin and checks out the target version.
#   4. Installs Python dependencies (production + dev extras).
#   5. Runs release checks: lint (ruff), format (black), type-check (mypy), tests.
#   6. Starts all servers and verifies they are running.
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
            NOMON_PI_HOST|NOMON_SSH_KEY) export "${key}=${val}" ;;
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

if [[ -z "${PI_HOST}" ]]; then
    echo "Error: NOMON_PI_HOST is not set." >&2
    echo "  Add it to ${ENV_FILE} or pass it as the second argument." >&2
    exit 1
fi

# ── SSH helpers ────────────────────────────────────────────────────────────────

SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15)
if [[ -n "${NOMON_SSH_KEY:-}" ]]; then
    SSH_OPTS+=(-i "${NOMON_SSH_KEY}")
fi

echo "==> Deploying nomothetic${VERSION:+ ${VERSION}} → ${PI_HOST}"

# ── Remote deployment ──────────────────────────────────────────────────────────
# All steps below run on the Pi inside a single SSH session.

ssh "${SSH_OPTS[@]}" "${PI_HOST}" 'bash -s "$@"' -- "${VERSION}" << 'END_REMOTE'
set -euo pipefail

readonly REQUESTED_VERSION="$1"
readonly REMOTE_DIR="${HOME}/perceptua-nomon/nomothetic"

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

    echo "  Restarting servers..." >&2
    ./scripts/start.sh stream 2>&1 || true
    ./scripts/start.sh api    2>&1 || true

    echo "!! Rollback complete. Servers restored to ${PREV_LABEL}." >&2
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
# Install with dev extras so we can run the full release check suite.

echo "==> Installing dependencies..."
uv sync --extra pi --extra web --extra api --extra telemetry --extra dev

# ── Release checks ─────────────────────────────────────────────────────────────

echo "==> [1/4] Lint (ruff)..."
uv run ruff check src/ tests/

echo "==> [2/4] Format check (black)..."
uv run black --check src/ tests/

echo "==> [3/4] Type check (mypy)..."
uv run mypy src/ tests/

echo "==> [4/4] Tests..."
uv run pytest tests/ -q

# ── Start servers & verify liveness ───────────────────────────────────────────

echo "==> Starting servers..."
./scripts/start.sh stream
./scripts/start.sh api

# Allow servers a moment to initialise before checking liveness.
sleep 4

echo "==> Verifying servers are running..."
declare -A SERVER_PIDS
for pid_file in /tmp/nomothetic-stream.pid /tmp/nomothetic-api.pid; do
    server="${pid_file#/tmp/nomothetic-}"
    server="${server%.pid}"
    if [[ ! -f "${pid_file}" ]]; then
        echo "Error: ${server} server PID file not found — it may have crashed." >&2
        exit 1
    fi
    pid="$(cat "${pid_file}")"
    if ! kill -0 "${pid}" 2>/dev/null; then
        echo "Error: ${server} server (PID ${pid}) is not running." >&2
        exit 1
    fi
    echo "  ${server}: running (PID ${pid}) ✓"
done

echo ""
echo "✓ nomothetic ${TARGET} deployed successfully to ${HOSTNAME}."
END_REMOTE
