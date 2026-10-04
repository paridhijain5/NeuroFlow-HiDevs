# Wiring Task 42 into the modules I could not see

Everything below uses `backend/monitoring/tracing.py` + `metrics.py`. `demo_pipeline.py` is a working reference.

| Module | Add |
|---|---|
| ingestion worker | `set_context(pipeline_id=...)`, then `span(SPANS.INGEST)` > `span(SPANS.EXTRACT.format(source_type=t))`, `SPANS.CHUNK`, `SPANS.EMBED`, `SPANS.WRITE_DB`; `metrics.record_ingested_doc(t)` after the DB write |
| ingestion endpoint | when you `LPUSH queue:ingest`, put `{"trace": tracing.inject_trace_context()}` in the job; in the worker `otel_context.attach(tracing.extract_trace_context(job["trace"]))` |
| retrieval stages | inside your `retrieve()`: `span(SPANS.DENSE)`, `SPARSE`, `METADATA`, `FUSION`, `RERANK`, `ASSEMBLE` (attributes: chunk_count, k, model...) - the parent `retrieval.pipeline` span + latency histogram already come from `traced_retrieval` |
| FastAPI app | `install_observability(app, redis=redis, otlp_endpoint="http://jaeger:4317")` |
| arq/ingest worker | call `metrics.refresh_from_redis(redis)` or just rely on the API process refresher for queue depth / breaker gauges |

Already instrumented in this zip: generation (`generator.py`), evaluation (`judge.py`, `llm.py`, `worker.py`),
circuit breaker (`circuit_breaker.py`), backpressure (`backpressure.py`), query entry point (`query.py`, `pipeline_runner.py`).
