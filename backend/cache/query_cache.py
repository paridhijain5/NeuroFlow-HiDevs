"""Redis cache for full query results (same query within the TTL returns the cached response).

The key includes a config fingerprint, so changing any quality parameter invalidates old entries.
Assumes an async stack (redis.asyncio). Wire it in around your pipeline entrypoint:

    cache = QueryCache(redis_client, ttl_seconds=params.query_cache_ttl_seconds,
                       enabled=params.query_cache_enabled, fingerprint=params.fingerprint())
    result = await cache.get_or_compute(query, lambda: run_pipeline(query))
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class AsyncRedisLike(Protocol):
    async def get(self, name: str) -> str | bytes | None: ...

    async def set(self, name: str, value: str, ex: int | None = None) -> object: ...


class QueryCache:
    def __init__(
        self,
        client: AsyncRedisLike,
        ttl_seconds: int = 1800,
        enabled: bool = True,
        fingerprint: str = "",
        namespace: str = "nf:qcache",
    ) -> None:
        self._client = client
        self._ttl = ttl_seconds
        self._enabled = enabled
        self._fingerprint = fingerprint
        self._ns = namespace
        self.hits = 0
        self.misses = 0

    def key(self, query: str) -> str:
        normalized = " ".join(query.lower().split())
        digest = hashlib.sha256(f"{normalized}::{self._fingerprint}".encode()).hexdigest()
        return f"{self._ns}:{digest}"

    async def get(self, query: str) -> dict[str, Any] | None:
        if not self._enabled:
            return None
        try:
            raw = await self._client.get(self.key(query))
        except Exception:  # cache must never break the request path
            logger.warning("query cache get failed", exc_info=True)
            return None
        if raw is None:
            self.misses += 1
            return None
        self.hits += 1
        loaded: dict[str, Any] = json.loads(raw)
        return loaded

    async def set(self, query: str, result: dict[str, Any]) -> None:
        if not self._enabled:
            return
        try:
            await self._client.set(self.key(query), json.dumps(result), ex=self._ttl)
        except Exception:
            logger.warning("query cache set failed", exc_info=True)

    async def get_or_compute(
        self, query: str, compute: Callable[[], Awaitable[dict[str, Any]]]
    ) -> dict[str, Any]:
        cached = await self.get(query)
        if cached is not None:
            return {**cached, "cached": True}
        result = await compute()
        await self.set(query, result)
        return {**result, "cached": False}
