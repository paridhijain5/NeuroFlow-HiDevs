# Enriching the OpenAPI docs (`GET /docs`)

Goal: a developer who has never used NeuroFlow can make a first call from Swagger UI alone.

## 1. App-level metadata
```python
from fastapi import FastAPI
from backend.api.openapi_meta import API_DESCRIPTION, API_TITLE, TAGS_METADATA

app = FastAPI(title=API_TITLE, description=API_DESCRIPTION, version="1.0.0",
              openapi_tags=TAGS_METADATA)
```

## 2. Every endpoint: summary, description, response_description, tags
```python
from backend.api.openapi_meta import COMMON_ERRORS

@router.post(
    "/query",
    tags=["Query"],
    summary="Ask a question over your documents",
    description=(
        "Runs the RAG pipeline: retrieves relevant chunks, reranks them and generates a cited answer.\n\n"
        "**When to use:** after at least one document has status `complete`.\n\n"
        "**Errors:** `404` if `pipeline_id` does not exist; `422` if `query` is empty; `429` when "
        "rate-limited; `503` while the LLM provider circuit breaker is open.\n\n"
        "**Performance:** typically 1-4 s (P95 target < 4 s). Identical queries within 30 minutes may "
        "be served from cache. For token-by-token output use `GET /query/{run_id}/stream`."
    ),
    response_description="The generated answer, its citations and the `run_id` used to fetch evaluations.",
    responses={**COMMON_ERRORS, 404: {"description": "Pipeline not found."}},
    response_model=QueryResponse,
)
async def run_query(body: QueryRequest) -> QueryResponse: ...
```

## 3. Every Pydantic field: `description`, `example` (and `json_schema_extra` for complex ones)
```python
from pydantic import BaseModel, ConfigDict, Field

class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Natural-language question to answer.",
                       examples=["What were the key findings of the report?"])
    pipeline_id: str = Field(..., description="ID of the pipeline to run.", examples=["default"])
    stream: bool = Field(False, description="If true, returns a `run_id` to stream tokens from.",
                         examples=[False])

    model_config = ConfigDict(json_schema_extra={
        "example": {"query": "What were the key findings?", "pipeline_id": "default", "stream": False}
    })
```
(Pydantic v1: use `Field(..., example=...)` and `class Config: schema_extra = {...}`.)

## 4. Audit
```bash
python scripts/check_openapi_docs.py --url https://<your-app>/openapi.json
```
Lists every endpoint missing a summary/description/tags/response description and every schema field
missing a description or example. Fix until it reports 0 problems, then do the "fresh pair of eyes"
test: ask someone who has never seen the project to make their first call using only `/docs`.
