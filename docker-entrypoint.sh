#!/bin/sh
# Start optional bundled services and the MovieSync web app in one container.
set -u

PANSOU_PID=""
OPENLIST_PID=""

if [ "${MOVIESYNC_BUNDLED_PANSOU_ENABLED:-true}" = "true" ]; then
    /usr/local/bin/pansou &
    PANSOU_PID=$!
fi

if [ "${MOVIESYNC_BUNDLED_OPENLIST_ENABLED:-true}" = "true" ]; then
    OPENLIST_DATA_DIR="${MOVIESYNC_OPENLIST_DATA_DIR:-/app/data/openlist}"
    mkdir -p "$OPENLIST_DATA_DIR"
    if [ -x /usr/local/bin/openlist ]; then
        /usr/local/bin/openlist --data "$OPENLIST_DATA_DIR" --log-std server &
        OPENLIST_PID=$!
        sleep 2
        if kill -0 "$OPENLIST_PID" 2>/dev/null; then
            echo "MovieSync: bundled OpenList runtime started (HTTP port 5244)." >&2
        else
            echo "MovieSync: bundled OpenList exited during startup; MovieSync will continue without it." >&2
            OPENLIST_PID=""
        fi
    else
        echo "MovieSync: bundled OpenList binary is missing; MovieSync will continue without it." >&2
    fi
fi

gunicorn --bind 0.0.0.0:5000 --workers 1 --threads 8 --timeout 120 main:app &
APP_PID=$!

shutdown() {
    kill -TERM "$APP_PID" 2>/dev/null || true
    if [ -n "$PANSOU_PID" ]; then
        kill -TERM "$PANSOU_PID" 2>/dev/null || true
    fi
    if [ -n "$OPENLIST_PID" ]; then
        kill -TERM "$OPENLIST_PID" 2>/dev/null || true
    fi
}

trap shutdown INT TERM
wait "$APP_PID"
STATUS=$?
shutdown
wait "$PANSOU_PID" 2>/dev/null || true
wait "$OPENLIST_PID" 2>/dev/null || true
exit "$STATUS"
