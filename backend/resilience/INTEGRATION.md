# Wiring Task 10 into your FastAPI app (backend/main.py)

```python
from backend.resilience import (
    APIRateLimitMiddleware, IngestQueueFull, ingest_queue_full_handler,
    enforce_ingest_backpressure, resilient_call,
)
from backend.resilience.health import build_health, check_redis

app.add_middleware(APIRateLimitMiddleware)                       # 429 + Retry-After
app.add_exception_handler(IngestQueueFull, ingest_queue_full_handler)  # 503 body

@app.post("/ingest")
async def ingest(...):
    decision = await enforce_ingest_backpressure()   # raises -> 503 if depth > 100
    ...  # enqueue the document as you do today
    body = {...your normal response...}
    if decision.warning:                              # depth > 50 -> 202 + warning
        return JSONResponse(status_code=202, content={**body, **decision.warning})
    return body

@app.get("/health")
async def health():
    checks = {
        "postgres": await your_postgres_check(),   # {"status": "ok", "latency_ms": 3}
        "redis": await check_redis(),
        "mlflow": await your_mlflow_check(),
    }
    return await build_health(checks)
```

Wrap every provider call (this is what `grep wait_for` will find in timeout_manager):

```python
result = await resilient_call("openai", "chat_completion",
                              lambda: provider.complete(messages),
                              pipeline_id=pid, pipeline_rpm=cfg.get("rate_limit_rpm"))
```

Assumptions to adjust if yours differ: Redis client uses `redis.asyncio` with
`decode_responses=True` (or call `set_redis(client)`), ingest queue key is `queue:ingest`
(env `INGEST_QUEUE_KEY`), worker count from env `WORKER_COUNT`.
