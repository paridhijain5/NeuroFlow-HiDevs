"""Pipeline CRUD, versions, run history, analytics, optimizer suggestions."""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import ValidationError

from backend.models.pipeline import PipelineConfig
from backend.services.pipeline_optimizer import suggest
from backend.services.pipeline_repo import DuplicateNameError
from backend.services.stats import compute_analytics

router = APIRouter()


def repo(request: Request):
    return request.app.state.pipeline_repo


def deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


async def _get_or_404(request: Request, pipeline_id: UUID, version: int | None = None):
    rec = await repo(request).get(pipeline_id, version)
    if rec is None:
        raise HTTPException(404, "pipeline not found")
    return rec


@router.post("/pipelines", status_code=201)
async def create_pipeline(config: PipelineConfig, request: Request):
    """Validated by PipelineConfig - unknown keys -> 422."""
    try:
        rec = await repo(request).create(config)
    except DuplicateNameError:
        raise HTTPException(409, f"a pipeline named '{config.name}' already exists")
    return {"pipeline_id": str(rec.id), "name": rec.name, "version": rec.version}


@router.get("/pipelines")
async def list_pipelines(request: Request, include_archived: bool = False):
    return {"pipelines": await repo(request).list(include_archived)}


@router.get("/pipelines/{pipeline_id}")
async def get_pipeline(pipeline_id: UUID, request: Request, version: int | None = Query(None, ge=1)):
    rec = await _get_or_404(request, pipeline_id, version)
    return {**rec.to_dict(), "aggregate_scores": await repo(request).aggregate_scores(pipeline_id)}


@router.get("/pipelines/{pipeline_id}/versions")
async def list_versions(pipeline_id: UUID, request: Request):
    await _get_or_404(request, pipeline_id)
    return {"versions": await repo(request).list_versions(pipeline_id)}


@router.patch("/pipelines/{pipeline_id}")
async def update_pipeline(pipeline_id: UUID, request: Request, patch: dict[str, Any] = Body(...)):
    """Partial update, deep-merged onto the current config and re-validated. Always a NEW version."""
    rec = await _get_or_404(request, pipeline_id)
    if rec.status == "archived":
        raise HTTPException(409, "pipeline is archived")
    if "name" in patch and patch["name"] != rec.name:
        raise HTTPException(422, "pipeline name is immutable")
    try:
        merged = PipelineConfig.model_validate(deep_merge(rec.config, patch))   # unknown keys rejected
    except ValidationError as e:
        raise HTTPException(422, json.loads(e.json()))
    new = await repo(request).update(pipeline_id, merged)
    return {"pipeline_id": str(pipeline_id), "previous_version": rec.version,
            "version": new.version, "config": new.config}


@router.delete("/pipelines/{pipeline_id}")
async def archive_pipeline(pipeline_id: UUID, request: Request):
    if not await repo(request).archive(pipeline_id):
        raise HTTPException(404, "pipeline not found")
    return {"pipeline_id": str(pipeline_id), "status": "archived"}


@router.get("/pipelines/{pipeline_id}/runs")
async def pipeline_runs(pipeline_id: UUID, request: Request,
                        limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    await _get_or_404(request, pipeline_id)
    items, total = await repo(request).list_runs(pipeline_id, limit, offset)
    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.get("/pipelines/{pipeline_id}/analytics")
async def pipeline_analytics(pipeline_id: UUID, request: Request):
    await _get_or_404(request, pipeline_id)
    return compute_analytics(await repo(request).fetch_runs_for_analytics(pipeline_id))


@router.get("/pipelines/{pipeline_id}/suggestions")
async def pipeline_suggestions(pipeline_id: UUID, request: Request):
    rec = await _get_or_404(request, pipeline_id)
    analytics = compute_analytics(await repo(request).fetch_runs_for_analytics(pipeline_id))
    return {"pipeline_id": str(pipeline_id), "version": rec.version,
            "based_on_runs": analytics["avg_eval_scores"]["evaluated_runs"],
            "suggestions": suggest(rec.config, analytics)}
