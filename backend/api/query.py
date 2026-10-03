"""POST /query and GET /query/{run_id}/stream (SSE via sse-starlette)."""
from __future__ import annotations

import asyncio
import json
import uuid
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from pipelines.generation.generator import stream_generation

router = APIRouter()
KEEPALIVE_SECONDS = 15


class QueryRequest(BaseModel):
    query: str
    pipeline_id: UUID
    stream: bool = False


# ADAPT: wire these to your app state / Task 35 retrieval + provider code.
async def retrieve(request: Request, pipeline_id: UUID, query: str):
    """Return ctx with .text, .chunks, .query_type, .sources (Task 35)."""
    raise NotImplementedError


def get_provider(request: Request):
    return request.app.state.provider


async def _settings_kwargs(request: Request, pipeline_id: UUID) -> dict:
    """Task 38: record pipeline version + apply the pipeline's generation settings (if repo is wired)."""
    repo = getattr(request.app.state, "pipeline_repo", None)
    if repo is None:
        return {}
    rec = await repo.get(pipeline_id)
    if rec is None or rec.status == "archived":
        return {}
    return {"pipeline_id": str(pipeline_id), "pipeline_version": rec.version,
            "gen_params": {"temperature": rec.config["generation"]["temperature"]},
            "auto_evaluate": rec.config["evaluation"]["auto_evaluate"]}


@router.post("/query")
async def create_query(body: QueryRequest, request: Request):
    run_id = str(uuid.uuid4())
    redis, db = request.app.state.redis, request.app.state.db

    if body.stream:
        # Defer work until the client connects to the stream endpoint.
        await redis.set(f"run:{run_id}", body.model_dump_json(), ex=300)
        return {"run_id": run_id}

    ctx = await retrieve(request, body.pipeline_id, body.query)
    text, final = [], {}
    async for ev in stream_generation(
        run_id=run_id, query=body.query, ctx=ctx, query_type=ctx.query_type,
        provider=get_provider(request), db=db, redis=redis,
        **await _settings_kwargs(request, body.pipeline_id),
    ):
        if ev["type"] == "token":
            text.append(ev["delta"])
        else:
            final = ev
    return {"run_id": run_id, "answer": "".join(text),
            "citations": final.get("citations", []),
            "invalid_citations": final.get("invalid_citations", [])}


def _sse(payload: dict) -> dict:
    return {"data": json.dumps(payload)}


@router.get("/query/{run_id}/stream")
async def stream_query(run_id: str, request: Request):
    redis, db = request.app.state.redis, request.app.state.db
    raw = await redis.get(f"run:{run_id}")
    if raw is None:
        raise HTTPException(404, "unknown or expired run_id")
    body = QueryRequest.model_validate_json(raw)
    queue: asyncio.Queue = asyncio.Queue()

    async def producer():
        try:
            await queue.put(_sse({"type": "retrieval_start"}))
            ctx = await retrieve(request, body.pipeline_id, body.query)
            await queue.put(_sse({"type": "retrieval_complete",
                                  "chunk_count": len(ctx.chunks),
                                  "sources": list(ctx.sources)}))
            async for ev in stream_generation(
                run_id=run_id, query=body.query, ctx=ctx, query_type=ctx.query_type,
                provider=get_provider(request), db=db, redis=redis,
        **await _settings_kwargs(request, body.pipeline_id),
            ):
                await queue.put(_sse(ev))
        finally:
            await queue.put(None)  # sentinel

    async def events():
        task = asyncio.create_task(producer())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    yield {"event": "keepalive", "data": "{}"}   # every 15s of silence
                    continue
                if item is None:
                    break
                yield item
                if await request.is_disconnected():
                    break
        finally:
            task.cancel()

    return EventSourceResponse(events())
