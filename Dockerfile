# MovieSync production image with the PanSou search API bundled in the same container.
# PanSou source is pinned to a reviewed upstream commit for reproducible builds.
ARG PANSOU_REF=7a73fbc3f62c6647183f33931477e055ee3d6847
FROM --platform=$BUILDPLATFORM golang:1.25-alpine AS pansou-builder
ARG TARGETARCH
ARG PANSOU_REF
RUN apk add --no-cache git ca-certificates
WORKDIR /src
RUN git init . \
    && git remote add origin https://github.com/fish2018/pansou.git \
    && git fetch --depth 1 origin "${PANSOU_REF}" \
    && git checkout --detach FETCH_HEAD
COPY patches/pansou-first-results.patch /tmp/pansou-first-results.patch
RUN git apply /tmp/pansou-first-results.patch \
    && gofmt -w api/search_first_handler.go service/search_service.go api/router.go

RUN mkdir -p /out
RUN CGO_ENABLED=0 GOOS=linux GOARCH=${TARGETARCH} \
    go build -trimpath -ldflags="-s -w -extldflags '-static'" -o /out/pansou .
RUN mkdir -p /out/licenses && cp LICENSE /out/licenses/PanSou-LICENSE

FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata gosu \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MOVIESYNC_DATA_DIR=/app/data \
    MOVIESYNC_PANSOU_URL=http://127.0.0.1:8888 \
    MOVIESYNC_BUNDLED_PANSOU_ENABLED=true \
    PORT=8888 \
    CACHE_PATH=/app/data/pansou/cache \
    CACHE_ENABLED=true \
    CACHE_TTL=60 \
    ASYNC_PLUGIN_ENABLED=true \
    ASYNC_RESPONSE_TIMEOUT=4 \
    ASYNC_MAX_BACKGROUND_WORKERS=20 \
    ASYNC_MAX_BACKGROUND_TASKS=100 \
    ASYNC_CACHE_TTL_HOURS=1 \
    TZ=Asia/Shanghai \
    MOVIESYNC_RUN_AS_NON_ROOT=true \
    MOVIESYNC_UID=10001 \
    MOVIESYNC_GID=10001

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=pansou-builder /out/pansou /usr/local/bin/pansou
COPY --from=pansou-builder /out/licenses/PanSou-LICENSE /usr/share/licenses/moviesync/PanSou-LICENSE

RUN mkdir -p /app/data /app/data/logs /app/data/pansou/cache \
    && chmod +x /app/docker-entrypoint.sh

VOLUME ["/app/data"]
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/healthz', timeout=3).close()"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
