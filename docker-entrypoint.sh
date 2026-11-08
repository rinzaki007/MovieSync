#!/bin/sh
# Run the application and bundled PanSou as an unprivileged UID by default.
# Set MOVIESYNC_RUN_AS_NON_ROOT=false to retain legacy root execution, or
# set MOVIESYNC_UID/GID to match the owner expected on the bind-mounted data dir.
if [ "${1:-}" != "--non-root" ] && [ "$(id -u)" = "0" ] && [ "${MOVIESYNC_RUN_AS_NON_ROOT:-true}" != "false" ]; then
    MOVIESYNC_UID="${MOVIESYNC_UID:-10001}"
    MOVIESYNC_GID="${MOVIESYNC_GID:-10001}"
    CURRENT_OWNER="$(stat -c '%u:%g' /app/data 2>/dev/null || true)"
    if [ "$CURRENT_OWNER" != "${MOVIESYNC_UID}:${MOVIESYNC_GID}" ]; then
        if ! chown -R "${MOVIESYNC_UID}:${MOVIESYNC_GID}" /app/data; then
            echo "无法调整 /app/data 的运行权限；请检查挂载目录，或设置 MOVIESYNC_RUN_AS_NON_ROOT=false" >&2
            exit 1
        fi
    fi
    exec gosu "${MOVIESYNC_UID}:${MOVIESYNC_GID}" "$0" --non-root
fi
if [ "${1:-}" = "--non-root" ]; then shift; fi
PANSOU_CONFIG_DIR="/app/data/pansou"
PANSOU_CONFIG_FILE="/app/data/pansou/pansou.env"
mkdir -p "$PANSOU_CONFIG_DIR"
if [ ! -f "$PANSOU_CONFIG_FILE" ]; then
    cp /app/pansou.defaults.env "$PANSOU_CONFIG_FILE"
fi
chmod 600 "$PANSOU_CONFIG_FILE" 2>/dev/null || true

# Explicit container environment variables take precedence over the persistent file.
CHANNELS_WAS_SET="${CHANNELS+x}"
CHANNELS_OVERRIDE="${CHANNELS-}"
PLUGINS_WAS_SET="${ENABLED_PLUGINS+x}"
PLUGINS_OVERRIDE="${ENABLED_PLUGINS-}"
. "$PANSOU_CONFIG_FILE"
if [ "$CHANNELS_WAS_SET" = "x" ]; then CHANNELS="$CHANNELS_OVERRIDE"; fi
if [ "$PLUGINS_WAS_SET" = "x" ]; then ENABLED_PLUGINS="$PLUGINS_OVERRIDE"; fi
export CHANNELS ENABLED_PLUGINS

# Start the bundled PanSou API and MovieSync web app in one container.
set -u

PANSOU_PID=""
if [ "${MOVIESYNC_BUNDLED_PANSOU_ENABLED:-true}" = "true" ]; then
    /usr/local/bin/pansou &
    PANSOU_PID=$!
fi

gunicorn --bind 0.0.0.0:5000 --workers 1 --threads 8 --timeout 120 main:app &
APP_PID=$!

shutdown() {
    kill -TERM "$APP_PID" 2>/dev/null || true
    if [ -n "$PANSOU_PID" ]; then
        kill -TERM "$PANSOU_PID" 2>/dev/null || true
    fi
}

trap shutdown INT TERM

process_running() {
    pid="$1"
    [ -r "/proc/$pid/stat" ] || return 1
    IFS=' ' read -r _ _ PROCESS_STATE _ < "/proc/$pid/stat" || return 1
    [ "$PROCESS_STATE" != "Z" ] && [ "$PROCESS_STATE" != "X" ]
}

# Supervise both processes. Repeated PanSou health failures exit the container,
# allowing Docker's configured restart policy to recover the bundled service.
STARTED_AT="$(date +%s)"
PANSOU_FAILURES=0
while process_running "$APP_PID"; do
    if [ -n "$PANSOU_PID" ]; then
        if ! process_running "$PANSOU_PID"; then
            echo "PanSou 进程意外退出，停止 MovieSync 以触发容器重启" >&2
            kill -TERM "$APP_PID" 2>/dev/null || true
            wait "$APP_PID" 2>/dev/null || true
            exit 1
        fi
        NOW="$(date +%s)"
        if [ $((NOW - STARTED_AT)) -ge 30 ]; then
            if python -c '
import json
import os
import urllib.request
url = os.getenv("MOVIESYNC_PANSOU_URL", "http://127.0.0.1:8888").rstrip("/") + "/api/health"
payload = json.load(urllib.request.urlopen(url, timeout=3))
raise SystemExit(0 if payload.get("status") == "ok" else 1)
' >/dev/null 2>&1; then
                PANSOU_FAILURES=0
            else
                PANSOU_FAILURES=$((PANSOU_FAILURES + 1))
                echo "PanSou 健康检查失败 (${PANSOU_FAILURES}/3)" >&2
                if [ "$PANSOU_FAILURES" -ge 3 ]; then
                    echo "PanSou 连续健康检查失败，停止容器以触发重启" >&2
                    shutdown
                    wait "$APP_PID" 2>/dev/null || true
                    exit 1
                fi
            fi
        fi
    fi
    sleep 10
done

wait "$APP_PID"
STATUS=$?
shutdown
wait "$PANSOU_PID" 2>/dev/null || true
exit "$STATUS"
