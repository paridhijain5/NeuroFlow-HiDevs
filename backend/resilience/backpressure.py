"""Ingestion backpressure based on Redis queue depth (LLEN queue:ingest).

    depth > 100  -> 503  {"error": "ingestion_queue_full", "queue_depth": N, "retry_after": 30}
    depth > 50   -> 202  + {"warning": "high_queue_depth", "estimated_wait_minutes": N}
    otherwise    -> 200
"""
import logging
import math
import os
from dataclasses import dataclass, field
from typing import Optional

from starlette.requests import Request
from starlette.responses import JSONResponse

from .redis_client import get_redis

logger = logging.getLogger("resilience.backpressure")

QUEUE_KEY = os.getenv("INGEST_QUEUE_KEY", "queue:ingest")
REJECT_DEPTH = 100
WARN_DEPTH = 50
RETRY_AFTER_SECONDS = 30
DOCS_PER_WORKER_PER_MINUTE = float(os.getenv("DOCS_PER_WORKER_PER_MINUTE", "6"))


def _worker_count() -> int:
    return max(1, int(os.getenv("WORKER_COUNT", "2")))


@dataclass
class BackpressureDecision:
    queue_depth: int
    status_code: int = 200
    body: Optional[dict] = field(default=None)  # warning / error payload

    @property
    def rejected(self) -> bool:
        return self.status_code == 503

    @property
    def warning(self) -> Optional[dict]:
        return self.body if self.status_code == 202 else None


class IngestQueueFull(Exception):
    def __init__(self, decision: BackpressureDecision):
        self.decision = decision
        super().__init__("ingestion queue full")


async def queue_depth(redis_client=None) -> int:
    return int(await (redis_client or get_redis()).llen(QUEUE_KEY))


def decide(depth: int) -> BackpressureDecision:
    if depth > REJECT_DEPTH:
        return BackpressureDecision(
            depth, 503,
            {"error": "ingestion_queue_full", "queue_depth": depth,
             "retry_after": RETRY_AFTER_SECONDS},
        )
    if depth > WARN_DEPTH:
        wait = math.ceil(depth / (_worker_count() * DOCS_PER_WORKER_PER_MINUTE))
        return BackpressureDecision(
            depth, 202,
            {"warning": "high_queue_depth", "estimated_wait_minutes": max(1, wait)},
        )
    return BackpressureDecision(depth, 200)


async def check_ingest_backpressure(redis_client=None) -> BackpressureDecision:
    depth = await queue_depth(redis_client)
    decision = decide(depth)
    if decision.status_code != 200:
        logger.warning("Ingest backpressure: depth=%s -> %s", depth, decision.status_code)
    return decision


async def enforce_ingest_backpressure(redis_client=None) -> BackpressureDecision:
    """Raises IngestQueueFull on 503; otherwise returns the decision (200 or 202)."""
    decision = await check_ingest_backpressure(redis_client)
    if decision.rejected:
        raise IngestQueueFull(decision)
    return decision


async def ingest_queue_full_handler(request: Request, exc: IngestQueueFull) -> JSONResponse:
    """Register with: app.add_exception_handler(IngestQueueFull, ingest_queue_full_handler)"""
    return JSONResponse(
        status_code=503,
        content=exc.decision.body,
        headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
    )
