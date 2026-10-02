# NeuroFlow-HiDevs

A production-grade multi-modal LLM/RAG platform: ingestion, hybrid retrieval, routed generation, automated evaluation, and a fine-tuning feedback loop.

## Repository layout

| Path | Purpose |
|------|---------|
| `backend/` | FastAPI service: REST API, retrieval, generation, evaluation workers |
| `frontend/` | Web UI (query, evaluation dashboard, pipeline management) |
| `pipelines/` | Ingestion and extraction pipelines (per modality) |
| `evaluation/` | LLM-as-judge metrics and aggregation jobs |
| `infra/` | Docker Compose, DB migrations, deployment config |
| `docs/` | Architecture, API contracts, data models, ADRs |

## Design documents (Task 1)
- [Architecture](docs/architecture.md)
- [API contracts](docs/api-contracts.md)
- [Data models](docs/data-models.md)
- ADRs: [001 vector store](docs/adr/001-vector-store.md) · [002 chunking](docs/adr/002-chunking-strategy.md) · [003 evaluation](docs/adr/003-evaluation-framework.md) · [004 model routing](docs/adr/004-model-routing.md)

## Stack
Python 3.11 · FastAPI · PostgreSQL + pgvector · Redis · MLflow · Server-Sent Events for streaming.
