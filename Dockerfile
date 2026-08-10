# Dockerfile ligero para probar ShipRAG en casa (perfil lite)
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SHIPRAG_PROFILE=lite \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY config ./config
COPY src ./src
COPY data/sample ./data/sample
COPY eval ./eval
COPY scripts ./scripts

RUN pip install --no-cache-dir -e ".[dev]"

EXPOSE 8080
VOLUME ["/app/data/indexes", "/app/data/raw", "/app/data/logs"]

# Ingesta sample al arrancar si el índice está vacío, luego sirve UI
CMD ["bash", "-lc", "shiprag --profile lite doctor && \
  (test -d data/indexes/lite/documents || shiprag --profile lite ingest data/sample) && \
  shiprag --profile lite serve --host 0.0.0.0 --port 8080"]
