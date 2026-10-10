# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
# --- web app ---
# base images pinned by digest (multi-arch index), Dependabot keeps tag and digest current
FROM node:26-alpine@sha256:143494b1da2945f061539253adc65e4f1569ddf07da2d384c022c791a9d90a4a AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npx tsc --noEmit && npx vite build --outDir /web/dist

# --- backend ---
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
LABEL org.opencontainers.image.source="https://github.com/Gr33ndev/OpenAmpere" \
      org.opencontainers.image.description="Lokale App für Solaranlagen mit Batteriespeicher (FoxESS H3, SAJ H2)" \
      org.opencontainers.image.licenses="MIT"
ENV PYTHONUNBUFFERED=1 \
    OPENAMPERE_CONFIG=/data/config.yaml \
    OPENAMPERE_STORAGE_PATH=/data/openampere.db
WORKDIR /app
COPY requirements.lock requirements-build.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock -r requirements-build.lock
COPY pyproject.toml README.md LICENSE NOTICE THIRD_PARTY_LICENSES.md ./
COPY src/ src/
COPY --from=web /web/dist/ src/openampere/web/
# licence texts are also kept as plain files in the image (MIT and the bundled components)
# built with the locked setuptools (no download of an unpinned build backend), then installed as a wheel
RUN pip wheel --no-cache-dir --no-deps --no-build-isolation -w /tmp/dist . \
    && pip install --no-cache-dir --no-deps /tmp/dist/*.whl && rm -rf /tmp/dist \
    && mkdir -p /usr/share/doc/openampere && cp LICENSE NOTICE THIRD_PARTY_LICENSES.md /usr/share/doc/openampere/ \
    && useradd --system --uid 1000 openampere && mkdir /data && chown openampere /data
USER openampere
VOLUME /data
EXPOSE 8080 8443
# a hung server counts as unhealthy, the updater then brings back the previous version (#167).
# Generous start period: the first start on a Raspberry Pi (database migrations) can take a while.
HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=3 CMD ["openampere", "healthcheck"]
CMD ["openampere"]
