"""Resilience data for GET /health.

    status: "ok"        all checks pass
            "degraded"  any circuit is open (or a non-critical check is failing)
            "critical"  Postgres or Redis unreachable
"""
import time
from typing import Dict, Optional

from .backpressure import queue_depth
from .circuit_breaker import circuit_breaker
from .redis_client import get_redis

PROVIDERS = ("openai", "anthropic")


async def check_redis(redis_client=None) -> dict:
    start = time.monotonic()
    try:
        await (redis_client or get_redis()).ping()
        return {"status": "ok", "latency_ms": int((time.monotonic() - start) * 1000)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc)[:200]}


async def get_circuit_breaker_status(redis_client=None) -> Dict[str, dict]:
    out = {}
    for name in PROVIDERS:
        cb = circuit_breaker(name)
        if redis_client is not None:
            cb._redis = redis_client
        try:
            out[name] = await cb.status()
        except Exception:  # noqa: BLE001
            out[name] = {"state": "unknown"}
    return out


def compute_status(checks: Dict[str, dict], breakers: Dict[str, dict]) -> str:
    if any(checks.get(k, {}).get("status") != "ok" for k in ("postgres", "redis")):
        return "critical"
    if any(b.get("state") == "open" for b in breakers.values()):
        return "degraded"
    if any(c.get("status") != "ok" for c in checks.values()):
        return "degraded"
    return "ok"


async def build_health(checks: Dict[str, dict], worker_count: Optional[int] = None,
                       redis_client=None) -> dict:
    """`checks` = {"postgres": {...}, "redis": {...}, "mlflow": {...}} from your existing checks."""
    breakers = await get_circuit_breaker_status(redis_client)
    try:
        depth = await queue_depth(redis_client)
    except Exception:  # noqa: BLE001
        depth = -1
    import os
    workers = worker_count if worker_count is not None else int(os.getenv("WORKER_COUNT", "2"))
    return {
        "status": compute_status(checks, breakers),
        "checks": {
            **checks,
            "circuit_breakers": breakers,
            "queue_depth": depth,
            "worker_count": workers,
        },
    }
