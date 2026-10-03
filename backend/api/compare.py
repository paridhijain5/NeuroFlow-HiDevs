"""POST /pipelines/compare - same query through two pipelines in parallel."""
from __future__ import annotations

import asyncio, time
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

router = APIRouter()
POLL_INTERVAL_S = 0.25


class CompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str
    pipeline_a_id: UUID
    pipeline_b_id: UUID


async def wait_for_eval(repo, run_id: str, timeout_s: float) -> float | None:
    """Eval jobs are queued (never awaited inline); poll briefly for the judge's score."""
    deadline = time.perf_counter() + timeout_s
    while True:
        score = await repo.get_eval_score(run_id)
        if score is not None or time.perf_counter() >= deadline:
            return score
        await asyncio.sleep(POLL_INTERVAL_S)


def _side(pipeline, res, eval_score):
    return {"pipeline_id": str(pipeline.id), "pipeline_name": pipeline.name,
            "pipeline_version": pipeline.version, "run_id": res.run_id, "generation": res.generation,
            "retrieval_latency_ms": res.retrieval_latency_ms, "total_latency_ms": res.total_latency_ms,
            "chunks_used": res.chunks_used, "eval_score": eval_score}


@router.post("/pipelines/compare")
async def compare_pipelines(body: CompareRequest, request: Request):
    repo, runner = request.app.state.pipeline_repo, request.app.state.pipeline_runner
    timeout_s = getattr(request.app.state, "eval_timeout_s", 15.0)

    a, b = await asyncio.gather(repo.get(body.pipeline_a_id), repo.get(body.pipeline_b_id))
    for rec, label in ((a, "pipeline_a_id"), (b, "pipeline_b_id")):
        if rec is None:
            raise HTTPException(404, f"{label}: pipeline not found")
        if rec.status == "archived":
            raise HTTPException(409, f"{label}: pipeline is archived")

    t0 = time.perf_counter()
    # both pipelines simultaneously; force_eval enqueues an evaluation job for each run
    res_a, res_b = await asyncio.gather(
        runner.run(a, body.query, force_eval=True), runner.run(b, body.query, force_eval=True))
    parallel_ms = int((time.perf_counter() - t0) * 1000)

    score_a, score_b = await asyncio.gather(
        wait_for_eval(repo, res_a.run_id, timeout_s), wait_for_eval(repo, res_b.run_id, timeout_s))

    return {"query": body.query,
            "pipeline_a": _side(a, res_a, score_a),
            "pipeline_b": _side(b, res_b, score_b),
            "timing": {"parallel_run_ms": parallel_ms,
                       "sequential_estimate_ms": res_a.total_latency_ms + res_b.total_latency_ms}}
