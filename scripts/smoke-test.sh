#!/bin/sh
# Read-only deployment smoke test for a running MovieSync instance.
# Host: MOVIESYNC_BASE_URL=http://127.0.0.1:8099 sh scripts/smoke-test.sh
# Container: docker exec -e MOVIESYNC_BASE_URL=http://127.0.0.1:5000 MovieSync sh /app/scripts/smoke-test.sh
set -eu

BASE_URL=${MOVIESYNC_BASE_URL:-http://127.0.0.1:8099}
BASE_URL=${BASE_URL%/}
TIMEOUT=${SMOKE_TIMEOUT:-10}
CLIENT=${SMOKE_HTTP_CLIENT:-auto}

case "$BASE_URL" in
    http://*|https://*) ;;
    *) printf 'ERROR: MOVIESYNC_BASE_URL must begin with http:// or https://\n' >&2; exit 2 ;;
esac

case "$CLIENT" in
    auto|curl|python) ;;
    *) printf 'ERROR: SMOKE_HTTP_CLIENT must be auto, curl, or python\n' >&2; exit 2 ;;
esac

HAS_CURL=0
if command -v curl >/dev/null 2>&1; then
    HAS_CURL=1
fi
if [ "$CLIENT" = "curl" ] && [ "$HAS_CURL" -ne 1 ]; then
    printf 'ERROR: SMOKE_HTTP_CLIENT=curl was requested but curl is unavailable.\n' >&2
    exit 2
fi
if [ "$CLIENT" = "auto" ] && [ "$HAS_CURL" -ne 1 ] \
    && ! command -v python3 >/dev/null 2>&1 \
    && ! command -v python >/dev/null 2>&1; then
    printf 'ERROR: curl or Python 3 is required to make HTTP requests.\n' >&2
    exit 2
fi

TMP_DIR=$(mktemp -d)
BODY_FILE="$TMP_DIR/response"
cleanup() {
    rm -rf "$TMP_DIR"
}
trap cleanup 0
trap 'exit 1' HUP INT TERM

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

request_http() {
    method=$1
    path=$2
    expected=$3
    payload=${4-}

    use_curl=0
    if [ "$CLIENT" = "curl" ] || { [ "$CLIENT" = "auto" ] && [ "$HAS_CURL" -eq 1 ]; }; then
        use_curl=1
    fi

    if [ "$use_curl" -eq 1 ]; then
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
    else
        if command -v python3 >/dev/null 2>&1; then
            python_cmd=python3
        elif command -v python >/dev/null 2>&1; then
            python_cmd=python
        else
            fail 'Python 3 is required when curl is unavailable'
        fi
        if ! actual=$("$python_cmd" - "$method" "${BASE_URL}${path}" "$BODY_FILE" "$TIMEOUT" "$payload" <<'PY'
import sys
import urllib.error
import urllib.request

method, url, output_path, timeout, payload = sys.argv[1:]
data = payload.encode("utf-8") if method == "POST" else None
headers = {"Content-Type": "application/json"} if data is not None else {}
request = urllib.request.Request(url, data=data, headers=headers, method=method)
try:
    with urllib.request.urlopen(request, timeout=float(timeout)) as response:
        status, body = response.status, response.read()
except urllib.error.HTTPError as exc:
    status, body = exc.code, exc.read()
except Exception as exc:
    print(f"HTTP request failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    sys.exit(1)
with open(output_path, "wb") as output:
    output.write(body)
sys.stdout.write(str(status))
PY
        ); then
            fail "$method $path could not connect or timed out"
        fi
    fi

    if [ "$actual" != "$expected" ]; then
        fail "$method $path returned HTTP $actual; expected $expected"
    fi
    printf 'PASS: %-5s %-28s HTTP %s\n' "$method" "$path" "$actual"
}

printf 'MovieSync read-only smoke test\n'
request_http GET /healthz 200
if ! grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"' "$BODY_FILE"; then
    fail 'GET /healthz returned HTTP 200 but status is not ok'
fi
printf 'PASS: /healthz reports status=ok\n'

request_http GET / 200
request_http GET /login 200
request_http GET /tasks 200
request_http GET /resource-select 200
request_http GET /admin 200
request_http GET /about 200
request_http GET /static/css/mobile.css 200

# This request has no session and no CSRF token. It must not reach resource search.
# A 401 here confirms the API remains protected without writing application data.
request_http POST /api/search-candidates 401 '{}'

printf '\nAll smoke checks passed. No login, transfer, directory creation, or config writes were performed.\n'
