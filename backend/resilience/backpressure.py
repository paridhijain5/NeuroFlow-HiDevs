"""Ingestion backpressure based on Redis queue depth (LLEN queue:ingest)."""
from __future__ import annotations

import math
from dataclasses import dataclass

from backend.monitoring import metrics

QUEUE_KEY = "queue:ingest"
WARN_DEPTH = 50          # depth  > 50  -> 202 + warning
FULL_DEPTH = 100         # depth  > 100 -> 503
RETRY_AFTER_S = 30
DOCS_PER_WORKER_PER_MIN = 6      # ADAPT: measured ingestion throughput of one worker
HEARTBEAT_PREFIX = "worker:heartbeat:"   # workers SET this key with EX 30 (see register_worker_heartbeat)


@dataclass
class BackpressureDecision:
    status: str                    # "ok" (200) | "warn" (202) | "full" (503)
    depth: int
    workers: int
    estimated_wait_minutes: int

    def body(self) -> dict:
        if self.status == "full":
            return {"error": "ingestion_queue_full", "queue_depth": self.depth, "retry_after": RETRY_AFTER_S}
        if self.status == "warn":
            return {"warning": "high_queue_depth", "estimated_wait_minutes": self.estimated_wait_minutes}
        return {}


async def count_workers(redis) -> int:
    n = 0
    async for _ in redis.scan_iter(match=f"{HEARTBEAT_PREFIX}*", count=100):
        n += 1
    return n


async def register_worker_heartbeat(redis, worker_id: str, ttl: int = 30) -> None:
    """Call every ~10s from each ingestion worker."""
    await redis.set(f"{HEARTBEAT_PREFIX}{worker_id}", "1", ex=ttl)


async def queue_depth(redis) -> int:
    return int(await redis.llen(QUEUE_KEY))


async def check_backpressure(redis) -> BackpressureDecision:
    depth = await queue_depth(redis)
    metrics.queue_depth.set(depth)
    workers = await count_workers(redis)
    wait = math.ceil(depth / (max(workers, 1) * DOCS_PER_WORKER_PER_MIN))
    status = "full" if depth > FULL_DEPTH else "warn" if depth > WARN_DEPTH else "ok"
    return BackpressureDecision(status, depth, workers, wait)
