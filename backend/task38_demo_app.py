"""Runnable demo (in-memory repo + mock runner, no DB/Redis/API keys):
    uvicorn backend.task38_demo_app:app --port 8000      (from the repo root)
    open http://localhost:8000/docs
"""
import asyncio, uuid, zlib
from datetime import datetime, timezone

from fastapi import FastAPI

from backend.api import compare, pipelines
from backend.services.pipeline_repo import InMemoryPipelineRepo
from backend.services.pipeline_runner import RunResult

W = {"faithfulness": 0.35, "answer_relevance": 0.30, "context_precision": 0.20, "context_recall": 0.15}


class MockRunner:
    """Latency and quality depend on the pipeline config, so A/B results differ."""

    def __init__(self, repo: InMemoryPipelineRepo, delay_scale: float = 1.0):
        self.repo, self.scale = repo, delay_scale

    async def run(self, pipeline, query: str, force_eval: bool = False) -> RunResult:
        cfg = pipeline.config
        r = cfg["retrieval"]
        retrieval_s = (0.05 + 0.004 * (r["dense_k"] + r["sparse_k"]) + (0.1 if r["reranker"] else 0)) * self.scale
        await asyncio.sleep(retrieval_s)
        gen_s = (0.2 + cfg["generation"]["max_context_tokens"] / 20000) * self.scale
        await asyncio.sleep(gen_s)

        run_id = str(uuid.uuid4())
        ret_ms, gen_ms = int(retrieval_s * 1000), int(gen_s * 1000)
        self.repo.add_run(run_id=run_id, pipeline_id=pipeline.id, pipeline_version=pipeline.version,
                          query=query, retrieval_latency_ms=ret_ms, latency_ms=gen_ms,
                          input_tokens=1500, output_tokens=200, status="complete",
                          created_at=datetime.now(timezone.utc))
        if force_eval or cfg["evaluation"]["auto_evaluate"]:
            asyncio.create_task(self._judge_later(run_id, cfg, query))   # stands in for the Task 37 worker
        return RunResult(run_id, f"[{pipeline.name} v{pipeline.version}] answer to: {query}",
                         ret_ms, ret_ms + gen_ms, r["top_k_after_rerank"])

    async def _judge_later(self, run_id, cfg, query):
        await asyncio.sleep(0.2)
        r = cfg["retrieval"]
        jitter = (zlib.crc32(query.encode()) % 5) / 100
        m = {"faithfulness": 0.85, "answer_relevance": 0.80,
             "context_precision": 0.85 if r["reranker"] else 0.45,
             "context_recall": 0.80 if r["dense_k"] >= 30 else 0.50}
        m = {k: round(v - jitter, 4) for k, v in m.items()}
        m["overall_score"] = round(sum(W[k] * m[k] for k in W), 4)
        self.repo.add_evaluation(run_id, **m)


def create_app(delay_scale: float = 1.0) -> FastAPI:
    app = FastAPI(title="NeuroFlow pipelines (demo)")
    app.state.pipeline_repo = InMemoryPipelineRepo()
    app.state.pipeline_runner = MockRunner(app.state.pipeline_repo, delay_scale)
    app.state.eval_timeout_s = 5.0
    app.include_router(pipelines.router)
    app.include_router(compare.router)
    return app


app = create_app()
