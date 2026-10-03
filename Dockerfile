# --- web app ---
FROM node:26-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npx tsc --noEmit && npx vite build --outDir /web/dist

# --- backend ---
FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/Gr33ndev/OpenAmpere" \
      org.opencontainers.image.description="Lokale App für Solaranlagen mit Batteriespeicher (FoxESS H3, SAJ H2)" \
      org.opencontainers.image.licenses="MIT"
ENV PYTHONUNBUFFERED=1 \
    OPENAMPERE_CONFIG=/data/config.yaml \
    OPENAMPERE_STORAGE_PATH=/data/openampere.db
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY pyproject.toml README.md LICENSE NOTICE THIRD_PARTY_LICENSES.md ./
COPY src/ src/
COPY --from=web /web/dist/ src/openampere/web/
# licence texts are also kept as plain files in the image (MIT and the bundled components)
RUN pip install --no-cache-dir --no-deps . \
    && mkdir -p /usr/share/doc/openampere && cp LICENSE NOTICE THIRD_PARTY_LICENSES.md /usr/share/doc/openampere/ \
    && useradd --system --uid 1000 openampere && mkdir /data && chown openampere /data
USER openampere
VOLUME /data
EXPOSE 8080
CMD ["openampere"]
