#!/bin/sh
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
wait "$APP_PID"
STATUS=$?
shutdown
wait "$PANSOU_PID" 2>/dev/null || true
exit "$STATUS"
