# Reuse the verified runtime and replace only reviewed application files.
FROM --platform=linux/amd64 ghcr.io/culvert529/gixradar:2.5.1-clean1@sha256:79d23c2b7f774830674dffa672e3f52eca72dddafba3153d4eca574d9293e214

COPY app/build/index.html /app/build/index.html
COPY app/build/static/js/main.747f18dd.js /app/build/static/js/main.747f18dd.js
COPY app/build/static/css/gix-zones.css /app/build/static/css/gix-zones.css
COPY app/build/static/js/gix-zones.js /app/build/static/js/gix-zones.js
COPY app/zones/backend/app/core/config.py /app/zones/backend/app/core/config.py
COPY app/zones/backend/app/services/analyzer.py /app/zones/backend/app/services/analyzer.py
COPY app/zones/backend/app/services/training.py /app/zones/backend/app/services/training.py
COPY app/zones/backend/app/services/quality.py /app/zones/backend/app/services/quality.py
COPY app/zones/backend/app/services/collector.py /app/zones/backend/app/services/collector.py
COPY app/zones/backend/app/services/pubg_api.py /app/zones/backend/app/services/pubg_api.py
COPY app/zones/backend/app/services/telemetry_parser.py /app/zones/backend/app/services/telemetry_parser.py
COPY app/zones/web/settings.js /app/zones/web/settings.js
COPY app/zones/web/settings.css /app/zones/web/settings.css
COPY app/zones/web/index.html /app/zones/web/index.html
COPY app/zones/backend/app/services/config_service.py /app/zones/backend/app/services/config_service.py
COPY app/zones/backend/app/db/connection.py /app/zones/backend/app/db/connection.py
COPY app/zones/backend/app/services/prediction.py /app/zones/backend/app/services/prediction.py
COPY app/zones/backend/app/main.py /app/zones/backend/app/main.py
COPY app/zones/backend/app/morse_native /app/zones/backend/app/morse_native
COPY app/zones/backend/app/db/migrations/012_morse_native.sql /app/zones/backend/app/db/migrations/012_morse_native.sql
COPY app/zones/backend/app/db/migrations/013_model_quality.sql /app/zones/backend/app/db/migrations/013_model_quality.sql
COPY app/zones/backend/app/db/migrations/014_serving_quality.sql /app/zones/backend/app/db/migrations/014_serving_quality.sql
COPY app/zones/backend/app/services/prequential.py /app/zones/backend/app/services/prequential.py
COPY app/zones/backend/app/db/migrations/015_prequential_quality.sql /app/zones/backend/app/db/migrations/015_prequential_quality.sql
COPY source-manifest.json /opt/gixradar-build.json

ARG VERSION=analyzer
ARG REVISION=unknown
LABEL org.opencontainers.image.version="${VERSION}" \
    org.opencontainers.image.revision="${REVISION}"
