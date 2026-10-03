"""Shared async Redis client for the resilience layer.

If your project already has an async Redis client, call `set_redis(your_client)`
once at startup (or pass `redis_client=` to each class) and this module is unused.
The client MUST be created with decode_responses=True.
"""
import os

import redis.asyncio as aioredis

_client = None


def get_redis():
    global _client
    if _client is None:
        _client = aioredis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
        )
    return _client


def set_redis(client) -> None:
    global _client
    _client = client
