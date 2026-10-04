"""Runnable demo that generates real traces + metrics (in-memory Redis/DB, mock LLM; no external services):

    pip install fastapi uvicorn "fakeredis[lua]" prometheus-client opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc
    export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317        # Jaeger (docker compose ... up -d jaeger)
    uvicorn backend.task42_demo_app:app --port 8000                 # from the repo root

    curl localhost:8000/metrics | head
    curl -X POST "localhost:8000/demo/ingest?source_type=pdf"
    curl -X POST "localhost:8000/demo/query?pipeline_id=legal"          # a full retrieval>generation>evaluation trace
    curl -X POST "localhost:8000/demo/query?pipeline_id=legal&fail=true"   # status="error" counter
    curl -X POST "localhost:8000/demo/breaker-trip?provider=anthropic"      # circuit gauges + trip counter + alert
    curl -X POST localhost:8000/demo/queue-depth/60                         # QueueDepthHigh alert after 5m
"""
import asyncio

import fakeredis
from fastapi import FastAPI

from backend.monitoring import metrics
from backend.monitoring.demo_pipeline import (FakeDB, FakeProvider, ingest_document, run_eval_worker_once,
                                              run_query_flow)
from backend.monitoring.install import install_observability
from backend.resilience.circuit_breaker import CircuitBreaker
from evaluation.judge import EvaluationJudge
from evaluation.mock_llm import MockEmbedder, MockLLM


def create_app(redis=None, exporter=None, simple_spans=False, refresh_seconds=2.0) -> FastAPI:
    redis = redis or fakeredis.FakeAsyncRedis(decode_responses=True)
    app = FastAPI(title="NeuroFlow observability (demo)")
    install_observability(app, redis=redis, exporter=exporter, simple_spans=simple_spans,
                          refresh_seconds=refresh_seconds)
    db = FakeDB()
    judge = EvaluationJudge(db, MockLLM(delay=0.005), MockEmbedder())

    @app.post("/demo/ingest")
    async def ingest(source_type: str = "pdf", pipeline_id: str = "legal"):
        n = await ingest_document(source_type, pipeline_id, "Liability is capped at the fees paid. " * 20)
        return {"chunks": n}

    @app.post("/demo/query")
    async def query(pipeline_id: str = "legal", fail: bool = False):
        out = await run_query_flow(redis, db, FakeProvider(fail=fail), pipeline_id, "What is the liability cap?")
        if not fail:
            asyncio.create_task(run_eval_worker_once(redis, judge))     # stands in for evaluation/worker.py
        return {"run_id": out["run_id"], "events": [e["type"] for e in out["events"]]}

    @app.post("/demo/breaker-trip")
    async def trip(provider: str = "anthropic"):
        cb = CircuitBreaker(provider, redis=redis)
        for _ in range(cb.failure_threshold):
            try:
                async with cb:
                    raise RuntimeError("provider 500")
            except RuntimeError:
                pass
        return await cb.snapshot()

    @app.post("/demo/queue-depth/{n}")
    async def depth(n: int):
        await redis.delete("queue:ingest")
        if n:
            await redis.rpush("queue:ingest", *["x"] * n)
        metrics.queue_depth.set(n)
        return {"queue_depth": n}

    return app


def __getattr__(name):          # `uvicorn backend.task42_demo_app:app` builds the app lazily (importing has no side effects)
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
