"""Shared OpenAPI metadata. Wire into your FastAPI app:

    from backend.api.openapi_meta import API_DESCRIPTION, API_TITLE, COMMON_ERRORS, TAGS_METADATA
    app = FastAPI(title=API_TITLE, description=API_DESCRIPTION, version="1.0.0",
                  openapi_tags=TAGS_METADATA)

and on each route:  @router.post("/query", tags=["Query"], summary="...", description="...",
                    response_description="...", responses={**COMMON_ERRORS})
See docs/openapi_enrichment.md for a full before/after example.
"""
from __future__ import annotations

from typing import Any

API_TITLE = "NeuroFlow API"
API_DESCRIPTION = """
NeuroFlow is a retrieval-augmented generation (RAG) platform.

**Typical flow:** ingest a document (`POST /ingest`) → wait until its status is `complete`
→ ask a question (`POST /query`) → optionally stream tokens (`GET /query/{run_id}/stream`)
→ read quality scores (`GET /evaluations`).

**Authentication:** send `Authorization: Bearer <API_KEY>` on every request. In Swagger UI click
**Authorize** (top right) and paste your key to try endpoints.

**Rate limits:** exceeding them returns `429`; retry with exponential backoff (the Python SDK does
this automatically).
"""

TAGS_METADATA: list[dict[str, str]] = [
    {"name": "Ingestion", "description": "Upload files or URLs and track ingestion status."},
    {"name": "Query", "description": "Ask questions over your ingested documents."},
    {"name": "Evaluation", "description": "Automated quality scores for query runs."},
    {"name": "Fine-Tuning", "description": "Create and monitor fine-tuning jobs."},
    {"name": "Admin", "description": "Operational endpoints. Require admin credentials."},
]

COMMON_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Missing or invalid API key."},
    422: {"description": "Validation error: the request body or parameters are malformed."},
    429: {"description": "Rate limit exceeded. Retry with backoff (see `Retry-After`)."},
}
