"""Explicit, context-appropriate timeouts for every external call.

    result = await timeout_manager.run("chat_completion", provider.complete(messages))

On asyncio.TimeoutError: log it, INCR timeouts:{task_type} in Redis, raise TimeoutError.

"Go further" (adaptive timeouts): successful latencies are kept in a Redis sorted
set (last 1000 calls). Every 25 calls the p95 is recomputed and the timeout becomes
p95 * 1.5 (clamped to 0.5x-3x of the base timeout). A warning is logged when p95
has been trending up over the last hour.
"""
import asyncio
import logging
import math
import time
import uuid
from typing import Awaitable, List, Optional, TypeVar

from redis.exceptions import RedisError

from .redis_client import get_redis

logger = logging.getLogger("resilience.timeout")

T = TypeVar("T")

TIMEOUTS = {
    "embedding": 10,         # seconds
    "chat_completion": 60,
    "reranking": 15,
    "evaluation": 120,       # evaluation is slower (multiple LLM calls)
    "file_extraction": 30,
    "url_fetch": 15,
}

HISTORY_SIZE = 1000
MIN_SAMPLES = 50
RECOMPUTE_EVERY = 25
ADAPTIVE_MULTIPLIER = 1.5
TREND_RATIO = 1.25


class OperationTimeoutError(TimeoutError):
    """Subclass of the builtin TimeoutError so `except TimeoutError` catches it."""

    def __init__(self, task_type: str, timeout: float):
        self.task_type = task_type
        self.timeout = timeout
        super().__init__(f"'{task_type}' timed out after {timeout:.1f}s")


def _p95(values: List[float]) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, math.ceil(0.95 * len(s)) - 1)]


class TimeoutManager:
    def __init__(self, redis_client=None, adaptive: bool = True):
        self._redis = redis_client
        self.adaptive = adaptive

    @property
    def redis(self):
        return self._redis or get_redis()

    async def get_timeout(self, task_type: str) -> float:
        if task_type not in TIMEOUTS:
            raise ValueError(f"Unknown task_type '{task_type}'. Known: {sorted(TIMEOUTS)}")
        base = float(TIMEOUTS[task_type])
        if not self.adaptive:
            return base
        try:
            raw = await self.redis.get(f"timeout:{task_type}:adaptive")
            return float(raw) if raw else base
        except RedisError:
            return base

    async def run(self, task_type: str, coro: Awaitable[T]) -> T:
        timeout = await self.get_timeout(task_type)
        start = time.monotonic()
        try:
            result = await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError:
            logger.error("Timeout: task_type=%s after %.1fs", task_type, timeout)
            try:
                await self.redis.incr(f"timeouts:{task_type}")
            except RedisError:
                logger.exception("Could not increment timeouts:%s", task_type)
            raise OperationTimeoutError(task_type, timeout) from None

        if self.adaptive:
            await self._record_latency(task_type, time.monotonic() - start)
        return result

    # ------------------------------------------------------------- adaptive
    async def _record_latency(self, task_type: str, latency: float) -> None:
        try:
            r = self.redis
            key = f"latency:{task_type}"
            now = time.time()
            await r.zadd(key, {f"{latency:.4f}:{uuid.uuid4().hex[:8]}": now})
            await r.zremrangebyrank(key, 0, -(HISTORY_SIZE + 1))  # keep newest 1000
            await r.expire(key, 86400)
            if await r.incr(f"latency:{task_type}:count") % RECOMPUTE_EVERY == 0:
                await self._recompute(task_type)
        except RedisError:
            logger.exception("Could not record latency for %s", task_type)

    async def _recompute(self, task_type: str) -> None:
        rows = await self.redis.zrange(f"latency:{task_type}", 0, -1, withscores=True)
        if len(rows) < MIN_SAMPLES:
            return
        lat = [float(m.split(":")[0]) for m, _ in rows]
        base = float(TIMEOUTS[task_type])
        p95 = _p95(lat)
        adaptive = min(max(p95 * ADAPTIVE_MULTIPLIER, base * 0.5), base * 3)
        await self.redis.set(f"timeout:{task_type}:adaptive", f"{adaptive:.3f}", ex=3600)

        # Trend: compare p95 of first vs second half of the last hour.
        cutoff = time.time() - 3600
        recent = [(float(m.split(":")[0]), ts) for m, ts in rows if ts >= cutoff]
        if len(recent) >= 20:
            mid = len(recent) // 2
            older, newer = [x for x, _ in recent[:mid]], [x for x, _ in recent[mid:]]
            if _p95(newer) > _p95(older) * TREND_RATIO:
                logger.warning(
                    "Latency for %s is trending up: p95 %.2fs -> %.2fs over the last hour",
                    task_type, _p95(older), _p95(newer),
                )


timeout_manager = TimeoutManager()
