# Wiring Task 13 into your existing FastAPI app

```python
# backend/main.py
from fastapi import Depends, UploadFile, File
from backend.security import (
    register_security, require_scope, validate_query_text, validate_pipeline_name,
    validate_document_url, check_query, set_llm_classifier, flag_chunks, redact_secrets,
)
from backend.security.validators import validate_upload_file

register_security(app)   # headers middleware + POST /auth/token + 400 handler

# plug your existing LLM client (async fn(prompt)->str) into layer 2:
async def classify(prompt: str) -> str:
    return await your_llm_client.complete(prompt, max_tokens=3)   # adapt
set_llm_classifier(classify)

# /health and /metrics: NO dependency (stay public)

@app.post("/query")
async def query(body: QueryIn, user=Depends(require_scope("query"))):
    text = validate_query_text(body.query)
    meta = await check_query(text)          # raises -> 400 query_rejected
    ...

@app.post("/ingest")
async def ingest(body: IngestIn, user=Depends(require_scope("ingest"))):
    if body.url: validate_document_url(body.url)
    # for uploads: await validate_upload_file(file)
    ...
    for chunk in chunks:                    # BEFORE embedding
        chunk["text"], _ = redact_secrets(chunk["text"], document_id=doc_id)
    flag_chunks(chunks)                     # prompt-injection flag (not reject)

@app.post("/pipelines")
async def create_pipeline(body: PipelineIn, user=Depends(require_scope("admin"))):
    body.name = validate_pipeline_name(body.name)
```
Fine-tuning endpoints also use `require_scope("admin")`.
