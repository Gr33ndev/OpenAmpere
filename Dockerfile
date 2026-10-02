# --- web app ---
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npx tsc --noEmit && npx vite build --outDir /web/dist

# --- backend ---
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    OPENAMPERE_CONFIG=/data/config.yaml \
    OPENAMPERE_STORAGE_PATH=/data/openampere.db
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/
COPY --from=web /web/dist/ src/openampere/web/
RUN pip install --no-cache-dir . && useradd --system --uid 1000 openampere && mkdir /data && chown openampere /data
USER openampere
VOLUME /data
EXPOSE 8080
CMD ["openampere"]
