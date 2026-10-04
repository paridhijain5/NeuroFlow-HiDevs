# syntax=docker/dockerfile:1

# ---------- Stage 1: Dependencies ----------
FROM python:3.11-slim AS deps
WORKDIR /app
# Build tools live ONLY in this throw-away stage (they never reach the runtime image).
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---------- Stage 2: Runtime (minimal, non-root) ----------
FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
# curl is needed by HEALTHCHECK (python:slim does not ship it)
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd -r neuroflow && useradd -r -g neuroflow -d /app -s /usr/sbin/nologin neuroflow
WORKDIR /app
COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin
# --chown avoids a second full-size layer that a separate `RUN chown -R` would create
COPY --chown=neuroflow:neuroflow . .
# writable app data dir (mounted as a volume in prod because the root fs is read-only)
RUN mkdir -p /app/data && chown neuroflow:neuroflow /app/data
USER neuroflow
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
