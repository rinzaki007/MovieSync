#!/bin/sh
set -eu

DATA_DIR="${MOVIESYNC_DATA_DIR:-/app/data}"
OUTPUT_DIR="${1:-./moviesync-backups}"

if [ ! -d "$DATA_DIR" ]; then
    echo "数据目录不存在: $DATA_DIR" >&2
    exit 1
fi

mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="$OUTPUT_DIR/moviesync-data-$STAMP.tar.gz"
TEMP_ARCHIVE="$ARCHIVE.tmp"

cleanup() {
    rm -f "$TEMP_ARCHIVE"
}
trap cleanup EXIT INT TERM

# Keep durable state (including dotfiles and .secret_key); omit regenerable caches/logs.
tar -czf "$TEMP_ARCHIVE" \
    --exclude='./logs' \
    --exclude='./cache' \
    --exclude='./pansou/cache' \
    --exclude='./backups' \
    -C "$DATA_DIR" .

mv "$TEMP_ARCHIVE" "$ARCHIVE"
trap - EXIT INT TERM
echo "$ARCHIVE"
