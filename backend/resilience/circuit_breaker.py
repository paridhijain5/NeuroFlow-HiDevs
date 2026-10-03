"""Redis-backed circuit breaker (CLOSED -> OPEN -> HALF_OPEN -> CLOSED).

State lives in Redis so it survives API restarts and is shared across workers:
    circuit:{name}:state
    circuit:{name}:failure_count
    circuit:{name}:opened_at
    circuit:{name}:half_open_calls   (admission counter for HALF_OPEN)

Usage:
    async with circuit_breaker("openai"):
        result = await provider.complete(messages)
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional, Tuple, Type

from redis.exceptions import RedisError

from .redis_client import get_redis

logger = logging.getLogger("resilience.circuit_breaker")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised immediately when a call is rejected by an open circuit."""

    def __init__(self, name: str, retry_in: float = 0.0):
        self.name = name
        self.retry_in = max(0.0, retry_in)
        super().__init__(f"Circuit '{name}' is open; retry in ~{self.retry_in:.0f}s")


# Atomically move OPEN -> HALF_OPEN exactly once even with many workers racing.
_TO_HALF_OPEN_LUA = """
if redis.call('GET', KEYS[1]) == 'open' then
    redis.call('SET', KEYS[1], 'half_open')
    redis.call('SET', KEYS[2], 0)
    return 1
end
return 0
"""


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        half_open_max_calls: int = 3,
        redis_client=None,
        ignore_exceptions: Tuple[Type[BaseException], ...] = (),
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self.ignore_exceptions = ignore_exceptions
        self._redis = redis_client

        self.k_state = f"circuit:{name}:state"
        self.k_failures = f"circuit:{name}:failure_count"
        self.k_opened_at = f"circuit:{name}:opened_at"
        self.k_half_calls = f"circuit:{name}:half_open_calls"

    @property
    def redis(self):
        return self._redis or get_redis()

    # ------------------------------------------------------------------ state
    async def get_state(self) -> CircuitState:
        raw = await self.redis.get(self.k_state)
        return CircuitState(raw) if raw else CircuitState.CLOSED

    async def get_failure_count(self) -> int:
        return int(await self.redis.get(self.k_failures) or 0)

    async def status(self) -> dict:
        """Snapshot used by GET /health."""
        state = await self.get_state()
        out: dict = {"state": state.value}
        if state == CircuitState.CLOSED:
            out["failure_count"] = await self.get_failure_count()
        else:
            opened = await self.redis.get(self.k_opened_at)
            if opened:
                out["opened_at"] = (
                    datetime.fromtimestamp(float(opened), tz=timezone.utc)
                    .strftime("%Y-%m-%dT%H:%M:%SZ")
                )
        return out

    async def _open(self) -> None:
        pipe = self.redis.pipeline()
        pipe.set(self.k_state, CircuitState.OPEN.value)
        pipe.set(self.k_opened_at, time.time())
        pipe.delete(self.k_half_calls)
        await pipe.execute()
        logger.warning("Circuit '%s' OPENED", self.name)

    async def _close(self) -> None:
        pipe = self.redis.pipeline()
        pipe.set(self.k_state, CircuitState.CLOSED.value)
        pipe.delete(self.k_failures, self.k_half_calls, self.k_opened_at)
        await pipe.execute()
        logger.info("Circuit '%s' CLOSED", self.name)

    async def reset(self) -> None:
        await self._close()

    # ------------------------------------------------------------ call hooks
    async def _before_call(self) -> None:
        r = self.redis
        state = await self.get_state()
        if state == CircuitState.CLOSED:
            return

        if state == CircuitState.OPEN:
            opened_at = float(await r.get(self.k_opened_at) or 0)
            elapsed = time.time() - opened_at
            if elapsed < self.recovery_timeout:
                raise CircuitOpenError(self.name, self.recovery_timeout - elapsed)
            moved = await r.eval(_TO_HALF_OPEN_LUA, 2, self.k_state, self.k_half_calls)
            if moved:
                logger.info("Circuit '%s' -> HALF_OPEN", self.name)

        # HALF_OPEN: only let `half_open_max_calls` trial calls through.
        admitted = await r.incr(self.k_half_calls)
        if admitted > self.half_open_max_calls:
            raise CircuitOpenError(self.name, 1)

    async def _on_success(self) -> None:
        state = await self.get_state()
        if state == CircuitState.HALF_OPEN:
            await self._close()  # any success while half-open -> closed
        elif state == CircuitState.CLOSED:
            await self.redis.delete(self.k_failures)  # failures must be consecutive

    async def _on_failure(self) -> None:
        state = await self.get_state()
        if state == CircuitState.HALF_OPEN:
            await self._open()  # any failure while half-open -> open again
            return
        if state == CircuitState.CLOSED:
            count = await self.redis.incr(self.k_failures)
            if count >= self.failure_threshold:
                await self._open()

    # ------------------------------------------------------- context manager
    async def __aenter__(self):
        try:
            await self._before_call()
        except CircuitOpenError:
            raise
        except RedisError:
            # Redis down: fail open so we don't take the whole API down with it.
            logger.exception("Circuit '%s': Redis unavailable, allowing call", self.name)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                await self._on_success()
            elif issubclass(exc_type, (asyncio.CancelledError, CircuitOpenError)):
                pass
            elif self.ignore_exceptions and issubclass(exc_type, self.ignore_exceptions):
                pass
            elif issubclass(exc_type, Exception):
                await self._on_failure()
        except RedisError:
            logger.exception("Circuit '%s': failed to record result", self.name)
        return False  # never swallow the original exception


# ---------------------------------------------------------------- registry
_breakers: Dict[str, CircuitBreaker] = {}


def circuit_breaker(name: str, **kwargs) -> CircuitBreaker:
    """Get (or lazily create) the shared breaker for `name`."""
    if name not in _breakers:
        _breakers[name] = CircuitBreaker(name, **kwargs)
    return _breakers[name]


def reset_registry() -> None:
    _breakers.clear()
