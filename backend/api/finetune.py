"""Fine-tuning API: /finetune/jobs, /finetune/jobs/{id}, /finetune/training-data/preview (+ DPO preview)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from pipelines.finetuning.extractor import QUALITY_THRESHOLD, extract_dpo_pairs, preview_training_data
from pipelines.finetuning.job_manager import InsufficientDataError

router = APIRouter()


class CreateJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_model: str = "gpt-4o-mini-2024-07-18"
    domain: str = "rag_generation"            # becomes the router task_type for the new model
    min_quality_score: float = Field(QUALITY_THRESHOLD, ge=0.0, le=1.0)


def _repo(request: Request):
    return request.app.state.finetune_repo


@router.post("/finetune/jobs", status_code=201)
async def create_job(body: CreateJobRequest, request: Request):
    """Trigger extraction + validation + submission. Returns job_id."""
    try:
        return await request.app.state.finetune_manager.create_job(
            body.base_model, body.domain, body.min_quality_score)
    except InsufficientDataError as e:
        raise HTTPException(422, {"error": "insufficient_training_data", **e.stats})


@router.get("/finetune/jobs")
async def list_jobs(request: Request):
    return {"jobs": await _repo(request).list_jobs()}


@router.get("/finetune/jobs/{job_id}")
async def get_job(job_id: str, request: Request):
    job = await _repo(request).get_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job          # includes mlflow_run_id / mlflow_run_url and training metrics


@router.get("/finetune/training-data/preview")
async def training_data_preview(request: Request, min_quality_score: float = Query(QUALITY_THRESHOLD, ge=0, le=1)):
    """5 sample pairs that WOULD be extracted now. No job, no files, no marking, no LLM calls."""
    return await preview_training_data(_repo(request), n=5, min_quality=min_quality_score)


@router.get("/finetune/dpo-data/preview")
async def dpo_preview(request: Request, limit: int = Query(5, ge=1, le=100)):
    pairs = extract_dpo_pairs(await _repo(request).fetch_rated_runs())
    return {"total_pairs": len(pairs), "samples": pairs[:limit]}
