# ---- 1. build the web UI ---------------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 2. runtime ---------------------------------------------------------------------
FROM python:3.12-slim AS runtime
ARG PRELOAD_MODEL=1
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    KOS_DATA_DIR=/data \
    KOS_MODELS_DIR=/opt/models \
    KOS_WEB_HOST=0.0.0.0 \
    KOS_MCP_HOST=0.0.0.0 \
    HF_HUB_DISABLE_TELEMETRY=1

# poppler-utils: PDF text extraction · tesseract: OCR for images
RUN apt-get update \
 && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/pyproject.toml ./
COPY backend/knowledge_os ./knowledge_os
COPY --from=web /web/dist ./knowledge_os/static
RUN pip install ".[local-embeddings]"

# Bake the small local embedding model into the image so first start needs no download.
RUN if [ "$PRELOAD_MODEL" = "1" ]; then \
      python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/opt/models')" ; \
    fi

RUN useradd --uid 1000 --create-home kos && mkdir -p /data && chown -R kos:kos /data /opt/models
USER kos
VOLUME ["/data"]
EXPOSE 8786 8796
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8786/api/health', timeout=4).status == 200 else 1)"
CMD ["python", "-m", "knowledge_os", "serve"]
