#!/bin/sh
# Read-only deployment smoke test for a running MovieSync instance.
# Usage: MOVIESYNC_BASE_URL=http://127.0.0.1:8099 sh scripts/smoke-test.sh
set -eu

BASE_URL=${MOVIESYNC_BASE_URL:-http://127.0.0.1:8099}
BASE_URL=${BASE_URL%/}
TIMEOUT=${SMOKE_TIMEOUT:-10}

case "$BASE_URL" in
    http://*|https://*) ;;
    *) printf 'ERROR: MOVIESYNC_BASE_URL must begin with http:// or https://\n' >&2; exit 2 ;;
esac

if ! command -v curl >/dev/null 2>&1; then
    printf 'ERROR: curl is required. Install curl or run this check from another host.\n' >&2
    exit 2
fi

TMP_DIR=$(mktemp -d)
BODY_FILE="$TMP_DIR/response"
cleanup() {
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT HUP INT TERM

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

check_status() {
    method=$1
    path=$2
    expected=$3
    payload=${4-}

    if [ "$method" = "POST" ]; then
        if ! actual=$(curl --silent --show-error \
            --connect-timeout 3 --max-time "$TIMEOUT" \
            --request POST \
            --header 'Content-Type: application/json' \
            --data "$payload" \
            --output "$BODY_FILE" --write-out '%{http_code}' \
            "${BASE_URL}${path}"); then
            fail "$method $path could not connect or timed out"
        fi
    else
        if ! actual=$(curl --silent --show-error \
            --connect-timeout 3 --max-time "$TIMEOUT" \
            --request "$method" \
            --output "$BODY_FILE" --write-out '%{http_code}' \
            "${BASE_URL}${path}"); then
            fail "$method $path could not connect or timed out"
        fi
    fi

    if [ "$actual" != "$expected" ]; then
        fail "$method $path returned HTTP $actual; expected $expected"
    fi
    printf 'PASS: %-5s %-28s HTTP %s\n' "$method" "$path" "$actual"
}

printf 'MovieSync read-only smoke test\n'
check_status GET /healthz 200
if ! grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"' "$BODY_FILE"; then
    fail 'GET /healthz returned HTTP 200 but status is not ok'
fi
printf 'PASS: /healthz reports status=ok\n'

check_status GET / 200
check_status GET /login 200
check_status GET /tasks 200
check_status GET /resource-select 200
check_status GET /admin 200
check_status GET /about 200
check_status GET /static/css/mobile.css 200

# This request has no session and no CSRF token. It must not reach resource search.
# A 401 here confirms the API remains protected without writing application data.
check_status POST /api/search-candidates 401 '{}'

printf '\nAll smoke checks passed. No login, transfer, directory creation, or config writes were performed.\n'
