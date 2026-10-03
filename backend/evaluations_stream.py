"""GET /evaluations/stream  — SSE feed of new evaluations (Task 11, Page 3).

Wire-up in backend/main.py:

    from backend.evaluations_stream import router as evaluations_stream_router
    app.include_router(evaluations_stream_router)

    # CORS so the Next.js dev server (and EventSource) can talk to the API:
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"],
                       allow_methods=["*"], allow_headers=["*"])

Then, right after an evaluation row is committed to Postgres, publish it:

    from backend.evaluations_stream import publish_evaluation
    await publish_evaluation(eval_dict)

Needs:  pip install sse-starlette
Route order: declare this BEFORE any `/evaluations/{id}` route so "stream" isn't treated as an id.
"""
import json
import logging

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from backend.resilience.redis_client import get_redis  # Task 10's shared async Redis client

logger = logging.getLogger("evaluations.stream")
router = APIRouter()
CHANNEL = "evaluations:new"


async def publish_evaluation(eval_dict: dict) -> None:
    """redis.publish("evaluations:new", json.dumps(eval_dict))"""
    await get_redis().publish(CHANNEL, json.dumps(eval_dict, default=str))


@router.get("/evaluations/stream")
async def evaluations_stream(request: Request):
    async def events():
        pubsub = get_redis().pubsub()
        await pubsub.subscribe(CHANNEL)
        try:
            while True:
                if await request.is_disconnected():
                    break
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg.get("data"):
                    yield {"event": "evaluation", "data": msg["data"]}
        finally:
            await pubsub.unsubscribe(CHANNEL)
            await pubsub.aclose()

    return EventSourceResponse(events(), ping=15)
