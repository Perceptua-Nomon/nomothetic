#!/usr/bin/env bash
# smoke-test-local.sh — nomon on-device integration smoke test
#
# Exercises all three Pi services in sequence:
#   [1] nomographic  — direct ArcadeDB query on nomon_local (port 2482)
#   [2] nomothetic   — device auth (pair if needed, or supply BEARER_TOKEN)
#   [3] nomopractic  — drive command via nomothetic API → IPC → hardware (300 ms)
#
# Run on the Pi:
#   ./scripts/smoke-test-local.sh
#
# If the device is already paired, supply an existing JWT:
#   BEARER_TOKEN=<token> ./scripts/smoke-test-local.sh
#
# Environment overrides:
#   LOCAL_ARCADEDB_ROOT_PASSWORD  (default: testpassword)
#   LOCAL_ARCADEDB_HTTP_PORT      (default: 2482)
#   BEARER_TOKEN                  existing JWT (skips pairing step)

set -euo pipefail

API="https://localhost:8443"
DB_PORT="${LOCAL_ARCADEDB_HTTP_PORT:-2482}"
DB_URL="http://127.0.0.1:${DB_PORT}"
DB_NAME="nomon_local"
DB_PASS="${LOCAL_ARCADEDB_ROOT_PASSWORD:-testpassword}"

CURL="curl --silent --fail --max-time 5 --insecure"

pass() { echo "    PASS: $*"; }
fail() { echo "    FAIL: $*" >&2; exit 1; }
step() { echo; echo "── $*"; }

# ── 1. nomographic: prove nomon_local schema is reachable ────────────────────
step "[1/3] nomographic — querying nomon_local DeviceState schema"

DB_RESP=$($CURL \
    --user "root:${DB_PASS}" \
    --request POST "${DB_URL}/api/v1/command/${DB_NAME}" \
    --header "Content-Type: application/json" \
    --data '{"language":"sql","command":"SELECT count(*) as n FROM DeviceState"}') \
    || fail "ArcadeDB not reachable at ${DB_URL} — is nomographic-local-db.service running?"

echo "$DB_RESP" | python3 -m json.tool
pass "ArcadeDB reachable, nomon_local schema intact"

# ── 2. nomothetic: authenticate (pair if unpaired, else use BEARER_TOKEN) ────
step "[2/3] nomothetic — device authentication"

STATUS=$($CURL "${API}/api/device/auth/status") \
    || fail "nomothetic-api not reachable at ${API} — is nomothetic-api.service running?"

PAIRED=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin)['paired'])")

if [[ "$PAIRED" == "False" ]]; then
    SECRET_FILE="/run/nomothetic/pairing-secret"
    [[ -r "$SECRET_FILE" ]] \
        || fail "${SECRET_FILE} not readable — check nomothetic-api.service RuntimeDirectory"
    SECRET=$(cat "$SECRET_FILE")
    echo "    Device unpaired — pairing now (secret from ${SECRET_FILE})"

    TOKENS=$($CURL \
        --request POST "${API}/api/device/auth/pair" \
        --header "Content-Type: application/json" \
        --data "{\"secret\":\"${SECRET}\",\"display_name\":\"smoke-test\"}") \
        || fail "Pairing request failed"

    TOKEN=$(echo "$TOKENS" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
    pass "Paired successfully"
else
    if [[ -z "${BEARER_TOKEN:-}" ]]; then
        echo "    Device is already paired."
        echo "    Provide an existing JWT to continue:"
        echo "      BEARER_TOKEN=<token> $0"
        echo "    Or restart nomothetic to reset pairing:"
        echo "      sudo systemctl restart nomothetic-api.service"
        exit 1
    fi
    TOKEN="$BEARER_TOKEN"
    pass "Using supplied BEARER_TOKEN"
fi

echo "    Token: ${TOKEN:0:30}..."

# ── 3. nomopractic: drive for 300 ms via API → IPC ───────────────────────────
step "[3/3] nomopractic — drive command (25%, 300 ms)"

DRIVE_RESP=$($CURL \
    --request POST "${API}/api/drive" \
    --header "Authorization: Bearer ${TOKEN}" \
    --header "Content-Type: application/json" \
    --data '{"speed_pct": 25, "ttl_ms": 300}') \
    || fail "Drive request failed — is nomopractic.service running? Check journalctl -u nomopractic"

echo "$DRIVE_RESP" | python3 -m json.tool
pass "Drive command acknowledged by nomopractic"

# ── Summary ──────────────────────────────────────────────────────────────────
echo
echo "================================================"
echo " Smoke test PASSED — all 3 services reachable"
echo "  nomographic : ArcadeDB nomon_local @ ${DB_URL}"
echo "  nomothetic  : HTTPS API @ ${API}"
echo "  nomopractic : IPC via drive endpoint"
echo "================================================"
