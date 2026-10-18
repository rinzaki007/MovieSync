#!/bin/sh
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
wait "$APP_PID"
STATUS=$?
shutdown
wait "$PANSOU_PID" 2>/dev/null || true
exit "$STATUS"
