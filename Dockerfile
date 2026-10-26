# Reuse the verified runtime and replace only reviewed application files.
FROM --platform=linux/amd64 ghcr.io/culvert529/gixradar:2.5.1-clean1@sha256:79d23c2b7f774830674dffa672e3f52eca72dddafba3153d4eca574d9293e214

COPY app/build/index.html /app/build/index.html
COPY app/build/static/js/gix-zones.js /app/build/static/js/gix-zones.js
COPY app/zones/backend/app/services/analyzer.py /app/zones/backend/app/services/analyzer.py
COPY app/zones/backend/app/services/training.py /app/zones/backend/app/services/training.py
COPY app/zones/web/settings.js /app/zones/web/settings.js
COPY app/zones/web/index.html /app/zones/web/index.html
COPY source-manifest.json /opt/gixradar-build.json

ARG VERSION=analyzer
ARG REVISION=unknown
LABEL org.opencontainers.image.version="${VERSION}" \
    org.opencontainers.image.revision="${REVISION}"
