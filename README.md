# NeuroFlow

> Multi-modal RAG platform with automated evaluation and a fine-tuning pipeline.

**Live demo:** https://YOUR-LIVE-URL  ·  **API docs:** https://YOUR-LIVE-URL/docs  ·  **Release:** [v1.0.0](../../releases/tag/v1.0.0)

## 1. What is NeuroFlow

NeuroFlow is a production-oriented, multi-modal retrieval-augmented generation (RAG) platform: it ingests PDFs, web pages and images, answers questions with cited, streamed responses, and scores every answer automatically. A fine-tuning pipeline (tracked in MLflow) closes the loop by turning evaluation results and curated data into improved models, with tracing, metrics and runbooks for operating it.

## 2. Architecture

<!-- ARCH_START -->
_Run `python scripts/fill_readme.py` to embed the diagram from `docs/architecture.md` here._
<!-- ARCH_END -->

| Subsystem | Responsibility |
|---|---|
| API (FastAPI) | Auth, validation, rate limiting, SSE streaming, OpenAPI docs |
| Ingestion workers | Parse, chunk, embed and index documents from a Redis-backed queue |
| Retrieval | Hybrid search (dense + sparse + metadata) fused with RRF, then cross-encoder reranking |
| Generation | LLM calls behind a circuit breaker with retries and provider fallback |
| Evaluation | Automated scoring of each query run (faithfulness, relevance, retrieval quality) |
| Fine-tuning | Dataset curation, training jobs and model registry/tracking in MLflow |
| Storage | PostgreSQL (documents, chunks, runs, evaluations), Redis (cache, queue, rate limits) |
| Observability | OpenTelemetry traces in Jaeger, Prometheus metrics, structlog JSON logs |

## 3. Key Features

- **Retrieval**
  - Hybrid retrieval (dense + sparse + metadata filters) with RRF fusion and cross-encoder reranking
  - Redis response cache for repeated queries
- **Generation**
  - Token-by-token streaming over Server-Sent Events
  - Circuit breaker around the LLM provider (`GET /health` reports state, `POST /admin/circuit-breaker/reset` for manual reset)
  - Retry with exponential backoff on HTTP 429
- **Evaluation & fine-tuning**
  - Every query run produces an evaluation record retrievable by `run_id`
  - MLflow-tracked fine-tuning jobs with rollback to the previous model
- **Operations**
  - Distributed tracing across API, retrieval, rerank and LLM spans
  - Daily APScheduler data-retention job (runs 90d, evaluations 180d, archived chunks)
  - Runbook for the five most likely production incidents (`docs/runbook.md`)
- **Developer experience**
  - Enriched OpenAPI spec (summaries, examples, error responses) at `/docs`
  - Installable async Python SDK (`pip install ./sdk`)

## 4. Quality Metrics

Source: `evaluation/quality_final.json` (real measured values).

<!-- METRICS_START -->
_Run `python scripts/fill_readme.py` to generate this table from `evaluation/quality_final.json`._
<!-- METRICS_END -->

## 5. Tech Stack

| Component | Technology | Why |
|---|---|---|
| API | FastAPI + Pydantic | Async, typed, auto-generated OpenAPI |
| Vector / relational store | PostgreSQL (+ pgvector) | One datastore for vectors, metadata and run history |
| Cache / queue | Redis | Low-latency cache, ingestion queue, rate limiting |
| Reranking | Cross-encoder | Precision gain over bi-encoder-only retrieval |
| Experiment tracking | MLflow | Reproducible fine-tuning runs and model versions |
| Tracing / metrics | OpenTelemetry, Jaeger, Prometheus | Find the slow span fast; alert on SLOs |
| Scheduling | APScheduler | Simple in-process daily retention job |
| Packaging / CI | Docker Compose, GitHub Actions | Reproducible deploys; automated lint, type-check, tests |

> Edit this table so it matches what you actually used.

## 6. Quick Start

```bash
git clone https://github.com/YOUR-USER/YOUR-REPO.git && cd YOUR-REPO
cp .env.example .env            # fill in required values
docker compose -f infra/docker-compose.prod.yml up --build
```

Then open http://localhost:8000/docs.

## 7. API Reference

<!-- API_START -->
_Run `python scripts/fill_readme.py --openapi https://YOUR-LIVE-URL/openapi.json` to generate this table._
<!-- API_END -->

## 8. SDK Usage

```python
import asyncio
from neuroflow import NeuroFlowClient

async def main():
    client = NeuroFlowClient(base_url="https://YOUR-LIVE-URL", api_key="YOUR_KEY")
    doc = await client.ingest_file("report.pdf", pipeline_id="default")   # waits until ingested
    print("Ingested:", doc)

    result = await client.query("What are the key findings?", pipeline_id="default")
    print(result)

    async for token in await client.query("Summarise it", pipeline_id="default", stream=True):
        print(token, end="", flush=True)

asyncio.run(main())
```

## 9. Configuration

See [`.env.example`](.env.example). Edit the lists below to match it.

- **Required:** `DATABASE_URL`, `REDIS_URL`, LLM provider API key, `API_KEY` / auth secret
- **Optional:** `OTEL_EXPORTER_OTLP_ENDPOINT`, `MLFLOW_TRACKING_URI`, cache TTLs, rate-limit settings, log level

## 10. Known Limitations

- Evaluation relies on an LLM judge, which can be biased and adds cost and latency.
- Single-region deployment; no automatic failover for Postgres or Redis.
- The circuit breaker is per-process; replicas do not share its state.
- Retention is a fixed daily job with hard-coded windows (not yet configurable per tenant).
- Multi-modal coverage for images is weaker than for text documents.

**What I would build next:** shared circuit-breaker state, per-tenant retention policies, online evaluation with human feedback, and a managed vector index for larger corpora.
