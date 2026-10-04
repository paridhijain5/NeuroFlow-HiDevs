"""Redis-backed circuit breaker. State lives in Redis so it survives API restarts and is shared by all workers.

Keys:  circuit:{name}:state | circuit:{name}:failure_count | circuit:{name}:opened_at
       circuit:{name}:half_open_calls   (calls admitted in the current half-open window)

Every transition is a Lua script, so concurrent workers can never over-admit half-open calls.
"""
from __future__ import annotations

import logging, time
from datetime import datetime, timezone
from typing import Callable

from backend.monitoring import metrics

from .errors import CircuitOpenError

log = logging.getLogger("neuroflow.resilience.circuit")

CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

_ACQUIRE = """
local state = redis.call('GET', KEYS[1]); if not state then state = 'closed' end
local now, recovery, max_calls = tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3])
if state == 'open' then
  local opened = tonumber(redis.call('GET', KEYS[3]) or '0')
  local elapsed = now - opened
  if elapsed >= recovery then
    state = 'half_open'
    redis.call('SET', KEYS[1], state); redis.call('SET', KEYS[4], 0)
  else
    return {0, state, tostring(recovery - elapsed)}
  end
end
if state == 'half_open' then
  local n = redis.call('INCR', KEYS[4])
  if n > max_calls then return {0, state, '1'} end
end
return {1, state, '0'}
"""

_SUCCESS = """
local state = redis.call('GET', KEYS[1]); if not state then state = 'closed' end
if state == 'half_open' then
  redis.call('SET', KEYS[1], 'closed'); redis.call('SET', KEYS[2], 0)
  redis.call('DEL', KEYS[3]); redis.call('SET', KEYS[4], 0)
  return 'closed_new'
elseif state == 'closed' then
  redis.call('SET', KEYS[1], 'closed')   -- materialise the key so /health lists every provider that was used
  redis.call('SET', KEYS[2], 0)          -- failures must be CONSECUTIVE
end
return state
"""

_FAILURE = """
local state = redis.call('GET', KEYS[1]); if not state then state = 'closed' end
local now, threshold = ARGV[1], tonumber(ARGV[2])
if state == 'half_open' then
  redis.call('SET', KEYS[1], 'open'); redis.call('SET', KEYS[3], now)
  redis.call('SET', KEYS[2], threshold); redis.call('SET', KEYS[4], 0)
  return 'open_new'
elseif state == 'closed' then
  local n = redis.call('INCR', KEYS[2])
  if n >= threshold then
    redis.call('SET', KEYS[1], 'open'); redis.call('SET', KEYS[3], now); redis.call('SET', KEYS[4], 0)
    return 'open_new'
  end
end
return state
"""

_default_redis = None


def configure(redis) -> None:
    global _default_redis
    _default_redis = redis


class CircuitBreaker:
    """
        async with CircuitBreaker("openai"):
            result = await provider.complete(messages)

    Fails open on Redis errors (logs a warning and lets the call through) - a broken Redis must not take
    every LLM call down with it; /health reports Redis as critical separately.
    """

    def __init__(self, name: str, failure_threshold: int = 5, recovery_timeout: int = 60,
                 half_open_max_calls: int = 3, *, redis=None, clock: Callable[[], float] = time.time,
                 failure_exceptions: tuple = (Exception,)):
        self.name, self.failure_threshold = name, failure_threshold
        self.recovery_timeout, self.half_open_max_calls = recovery_timeout, half_open_max_calls
        self.redis = redis or _default_redis
        if self.redis is None:
            raise RuntimeError("CircuitBreaker needs a Redis client: pass redis= or call circuit_breaker.configure()")
        self.clock, self.failure_exceptions = clock, failure_exceptions
        p = f"circuit:{name}"
        self._keys = [f"{p}:state", f"{p}:failure_count", f"{p}:opened_at", f"{p}:half_open_calls"]
        self._acquire = self.redis.register_script(_ACQUIRE)
        self._success = self.redis.register_script(_SUCCESS)
        self._failure = self.redis.register_script(_FAILURE)

    # ---- read-only helpers
    async def snapshot(self) -> dict:
        state, count, opened = await self.redis.mget(self._keys[:3])
        out = {"state": state or CLOSED, "failure_count": int(count or 0)}
        if opened and out["state"] != CLOSED:
            out["opened_at"] = datetime.fromtimestamp(float(opened), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return out

    async def fail_fast_if_open(self) -> None:
        """Read-only pre-check (does not consume a half-open slot) so callers can skip e.g. rate-limit tokens."""
        try:
            state, opened = await self.redis.mget([self._keys[0], self._keys[2]])
        except Exception:
            return
        if state == OPEN:
            remaining = self.recovery_timeout - (self.clock() - float(opened or 0))
            if remaining > 0:
                raise CircuitOpenError(self.name, remaining)

    # ---- async context manager
    async def __aenter__(self):
        try:
            allowed, state, retry = await self._acquire(
                keys=self._keys, args=[self.clock(), self.recovery_timeout, self.half_open_max_calls])
        except Exception:
            log.warning("circuit %s: Redis unavailable, failing open", self.name, exc_info=True)
            return self
        if not int(allowed):
            raise CircuitOpenError(self.name, float(retry))
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                if await self._success(keys=self._keys, args=[]) == "closed_new":
                    metrics.set_breaker_state(self.name, "closed")
            elif issubclass(exc_type, Exception) and issubclass(exc_type, self.failure_exceptions):
                new_state = await self._failure(keys=self._keys, args=[self.clock(), self.failure_threshold])
                if new_state == "open_new":      # a real transition (not another failure while already open)
                    log.error("circuit %s is OPEN (%s)", self.name, exc_type.__name__)
                    metrics.record_circuit_trip(self.name)
            # cancellations / GeneratorExit are neither success nor failure
        except Exception:
            log.warning("circuit %s: could not record outcome", self.name, exc_info=True)
        return False

    async def call(self, fn, *args, **kwargs):
        async with self:
            return await fn(*args, **kwargs)


_registry: dict[str, CircuitBreaker] = {}


def circuit_breaker(name: str, **kwargs) -> CircuitBreaker:
    """Shared instance per name (state itself is in Redis anyway)."""
    if name not in _registry:
        _registry[name] = CircuitBreaker(name, **kwargs)
    return _registry[name]
