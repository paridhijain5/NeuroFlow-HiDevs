"""Runs one query through one named pipeline (retrieval -> streaming generation)."""
from __future__ import annotations

import time, uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from backend.models.pipeline import PipelineConfig
from pipelines.generation.generator import stream_generation


@dataclass
class RunResult:
    run_id: str
    generation: str
    retrieval_latency_ms: int
    total_latency_ms: int
    chunks_used: int


class PipelineRunner(Protocol):
    async def run(self, pipeline: Any, query: str, force_eval: bool = False) -> RunResult: ...


class DefaultPipelineRunner:
    """
    ADAPT: `retrieve(cfg: PipelineConfig, pipeline_id, query)` must return ctx (.text, .chunks, .query_type)
    and honour cfg.retrieval (dense_k, sparse_k, reranker, top_k_after_rerank, query_expansion).
    max_context_tokens / system_prompt_variant should be applied inside your context assembly / prompt builder.
    NOTE: not exercised by the offline tests.
    """

    def __init__(self, *, retrieve: Callable[..., Awaitable[Any]], provider, db, redis):
        self.retrieve, self.provider, self.db, self.redis = retrieve, provider, db, redis

    async def run(self, pipeline, query: str, force_eval: bool = False) -> RunResult:
        cfg = PipelineConfig.model_validate(pipeline.config)
        run_id, t0 = str(uuid.uuid4()), time.perf_counter()
        ctx = await self.retrieve(cfg, pipeline.id, query)
        retrieval_ms = int((time.perf_counter() - t0) * 1000)

        parts: list[str] = []
        async for ev in stream_generation(
            run_id=run_id, query=query, ctx=ctx, query_type=ctx.query_type,
            provider=self.provider, db=self.db, redis=self.redis,
            pipeline_id=str(pipeline.id), pipeline_version=pipeline.version,
            retrieval_latency_ms=retrieval_ms,
            gen_params={"temperature": cfg.generation.temperature},
            auto_evaluate=force_eval or cfg.evaluation.auto_evaluate,
        ):
            if ev["type"] == "token":
                parts.append(ev["delta"])
            elif ev["type"] == "error":
                raise RuntimeError(ev.get("message", "generation failed"))
        return RunResult(run_id, "".join(parts), retrieval_ms,
                         int((time.perf_counter() - t0) * 1000), len(ctx.chunks))
