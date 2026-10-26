FROM --platform=linux/amd64 node:18.5.0-bullseye-slim@sha256:85f3bebc533d681260e78800bfa67a23531abc7dd99fa085d082a81b89d63f91 AS node-runtime
FROM --platform=linux/amd64 python:3.12-slim-bookworm AS runtime-base

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node

FROM runtime-base AS python-dependencies
COPY container/zones/requirements.txt /opt/gixradar-requirements.txt
COPY container/zones/wheels/ /opt/gixradar-wheels/
RUN python -m pip install --no-cache-dir --no-index --find-links=/opt/gixradar-wheels \
    --target=/opt/gixradar-python -r /opt/gixradar-requirements.txt \
    && test "$(node --version)" = "v18.5.0"

FROM runtime-base
ARG VERSION=analyzer
ARG REVISION=unknown
LABEL org.opencontainers.image.source="https://github.com/culvert529/gixradar" \
    org.opencontainers.image.version="${VERSION}" \
    org.opencontainers.image.revision="${REVISION}" \
    io.gixradar.source-release-sha256="d5732fecf94c06eae05c0221347a29c24cf9aeb9a1c864f4e3a382cdb0956014"
COPY --from=python-dependencies /opt/gixradar-python/ /opt/gixradar-python/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp PYTHONPATH=/opt/gixradar-python
ENV OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
WORKDIR /app/service
# Copy only the approved d573 release context; runtime code has no host bind mount.
COPY app/ /app/
COPY source-manifest.json /opt/gixradar-build.json
COPY container/entrypoint.sh container/http-healthcheck.cjs /opt/gixradar-container/
COPY container/zones/gateway.cjs container/zones/gateway-core.cjs container/zones/supervise.py /opt/gixradar-container/zones/
USER 1000:10
EXPOSE 8888/tcp
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["node", "/opt/gixradar-container/http-healthcheck.cjs"]
ENTRYPOINT ["/bin/sh", "/opt/gixradar-container/entrypoint.sh"]
