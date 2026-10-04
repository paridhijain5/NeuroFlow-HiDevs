# neuroflow-sdk

Async Python SDK for the NeuroFlow AI platform.

```bash
pip install ./sdk            # or: pip install -i https://test.pypi.org/simple/ neuroflow-sdk
```

## 10-line quickstart

```python
import asyncio
from neuroflow import NeuroFlowClient

async def main():
    async with NeuroFlowClient("https://your-app.up.railway.app", api_key="YOUR_KEY") as client:
        doc = await client.ingest_file("report.pdf", pipeline_id="default")   # waits until complete
        async for token in client.query("Summarise the report", "default", stream=True):
            print(token, end="", flush=True)

asyncio.run(main())
```

## API

| Method | Description |
|---|---|
| `ingest_file(path, pipeline_id=None, wait=True)` | Upload + ingest; polls until `complete` |
| `ingest_url(url, pipeline_id=None, wait=True)` | Ingest a URL |
| `await query(q, pipeline_id)` | Returns `QueryResult` (`answer`, `citations`, `run_id`) |
| `query(q, pipeline_id, stream=True)` | Async iterator of tokens (SSE) |
| `get_evaluation(run_id, wait=True)` | `EvaluationResult` with scores; polls until ready |
| `list_pipelines()` / `create_pipeline(config)` | Manage pipelines |

## Behaviour
- **Auth:** sends `Authorization: Bearer <api_key>` on every request.
- **Rate limits:** retries HTTP 429 with exponential backoff (honours `Retry-After`), up to `max_retries` (default 5), then raises `RateLimitError`.
- **Streaming:** `stream=True` submits the query, then reads the Server-Sent Events stream at `/query/{run_id}/stream`.
- **Polling:** ingestion and evaluation are polled every `poll_interval` s until `poll_timeout`; failures raise `IngestionError` / `NeuroFlowTimeout`.
- **Errors:** `AuthenticationError`, `NotFoundError`, `RateLimitError`, `APIError` (all subclass `NeuroFlowError`).

See `examples/quickstart.py` for a runnable script.
