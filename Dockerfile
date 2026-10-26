# Reuse the verified runtime and replace only the reviewed frontend files.
FROM --platform=linux/amd64 ghcr.io/culvert529/gixradar:2.5.1-clean1@sha256:79d23c2b7f774830674dffa672e3f52eca72dddafba3153d4eca574d9293e214

COPY app/build/index.html /app/build/index.html
COPY app/build/static/js/gix-zones.js /app/build/static/js/gix-zones.js
COPY source-manifest.json /opt/gixradar-build.json

ARG VERSION=analyzer
ARG REVISION=unknown
LABEL org.opencontainers.image.version="${VERSION}" \
    org.opencontainers.image.revision="${REVISION}"
