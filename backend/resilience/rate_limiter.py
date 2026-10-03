"""Rate limiting backed by Redis.

1. Global LLM limit  -> token bucket  rpb:{provider}:tokens
2. Per-pipeline limit -> token bucket  rpb:pipeline:{pipeline_id}:tokens
3. API endpoints      -> sliding window (sorted set) + FastAPI middleware, 429 + Retry-After

All state changes happen inside Lua scripts so they are atomic across workers.
"""
import asyncio
import logging
import math
import uuid
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .redis_client import get_redis

logger = logging.getLogger("resilience.rate_limiter")


class RateLimitExceeded(Exception):
    def __init__(self, scope: str, retry_after: float):
        self.scope = scope
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded for {scope}; retry in {retry_after:.1f}s")


# ---------------------------------------------------------------- token bucket
_TOKEN_BUCKET_LUA = """
local key      = KEYS[1]
local capacity = tonumber(ARGV[1])
local rate     = tonumber(ARGV[2])
local wanted   = tonumber(ARGV[3])
local t        = redis.call('TIME')
local now      = tonumber(t[1]) + tonumber(t[2]) / 1000000

local data   = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts     = tonumber(data[2])
if tokens == nil then tokens = capacity; ts = now end

tokens = math.min(capacity, tokens + math.max(0, now - ts) * rate)

local allowed = 0
local wait = 0
if tokens >= wanted then
    tokens = tokens - wanted
    allowed = 1
else
    wait = (wanted - tokens) / rate
end

redis.call('HSET', key, 'tokens', tostring(tokens), 'ts', tostring(now))
redis.call('EXPIRE', key, math.ceil(capacity / rate) * 2 + 60)
return {allowed, tostring(wait), tostring(tokens)}
"""


class TokenBucket:
    def __init__(self, key: str, capacity: float, refill_rate: float, redis_client=None):
        self.key = key
        self.capacity = capacity
        self.refill_rate = refill_rate  # tokens per second
        self._redis = redis_client

    @property
    def redis(self):
        return self._redis or get_redis()

    async def try_acquire(self, tokens: int = 1) -> Tuple[bool, float]:
        """Returns (allowed, seconds_to_wait_if_denied)."""
        allowed, wait, _ = await self.redis.eval(
            _TOKEN_BUCKET_LUA, 1, self.key, self.capacity, self.refill_rate, tokens
        )
        return bool(int(allowed)), float(wait)

    async def acquire(self, tokens: int = 1, max_wait: float = 30.0) -> None:
        """Wait-and-retry until a token is available (bucket empty -> wait)."""
        waited = 0.0
        while True:
            allowed, wait = await self.try_acquire(tokens)
            if allowed:
                return
            if waited + wait > max_wait:
                raise RateLimitExceeded(self.key, wait)
            sleep_for = min(max(wait, 0.01), 1.0)
            await asyncio.sleep(sleep_for)
            waited += sleep_for

    async def available(self) -> float:
        raw = await self.redis.hget(self.key, "tokens")
        return float(raw) if raw is not None else float(self.capacity)


# ----------------------------------------------------------- global LLM limit
# provider -> (requests per minute). Bucket starts full and refills rpm/60 per sec.
PROVIDER_RPM: Dict[str, int] = {
    "openai": 3000,   # gpt-4o-mini: 3000 req/min -> refill 50 tokens/sec
    "anthropic": 1000,
}


def llm_rate_limiter(provider: str, redis_client=None) -> TokenBucket:
    rpm = PROVIDER_RPM.get(provider, 3000)
    return TokenBucket(f"rpb:{provider}:tokens", capacity=rpm, refill_rate=rpm / 60.0,
                       redis_client=redis_client)


# ------------------------------------------------------------ per-pipeline limit
class PipelineRateLimiter:
    """Pipeline config may specify {"rate_limit_rpm": 60}."""

    def __init__(self, redis_client=None):
        self._redis = redis_client

    def bucket(self, pipeline_id: str, rpm: int) -> TokenBucket:
        return TokenBucket(f"rpb:pipeline:{pipeline_id}:tokens", capacity=rpm,
                           refill_rate=rpm / 60.0, redis_client=self._redis)

    async def check(self, pipeline_id: str, rpm: Optional[int]) -> None:
        """Raises RateLimitExceeded (no waiting) if the pipeline is over its rpm."""
        if not rpm:
            return
        allowed, wait = await self.bucket(pipeline_id, rpm).try_acquire(1)
        if not allowed:
            raise RateLimitExceeded(f"pipeline:{pipeline_id}", wait)

    async def acquire(self, pipeline_id: str, rpm: Optional[int], max_wait: float = 30.0) -> None:
        """Same as check() but waits for a token instead of failing fast."""
        if rpm:
            await self.bucket(pipeline_id, rpm).acquire(1, max_wait=max_wait)


# ------------------------------------------------------------- sliding window
_SLIDING_WINDOW_LUA = """
local key    = KEYS[1]
local window = tonumber(ARGV[1])   -- seconds
local limit  = tonumber(ARGV[2])
local member = ARGV[3]
local t      = redis.call('TIME')
local now    = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)  -- ms

redis.call('ZREMRANGEBYSCORE', key, 0, now - window * 1000)
local count = redis.call('ZCARD', key)
if count < limit then
    redis.call('ZADD', key, now, member)
    redis.call('EXPIRE', key, window + 1)
    return {1, limit - count - 1, 0}
end
local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
local retry = math.ceil((tonumber(oldest[2]) + window * 1000 - now) / 1000)
if retry < 1 then retry = 1 end
return {0, 0, retry}
"""


@dataclass
class WindowResult:
    allowed: bool
    remaining: int
    retry_after: int


class SlidingWindowRateLimiter:
    def __init__(self, redis_client=None):
        self._redis = redis_client

    @property
    def redis(self):
        return self._redis or get_redis()

    async def check(self, key: str, limit: int, window_seconds: int) -> WindowResult:
        allowed, remaining, retry = await self.redis.eval(
            _SLIDING_WINDOW_LUA, 1, key, window_seconds, limit, uuid.uuid4().hex
        )
        return WindowResult(bool(int(allowed)), int(remaining), int(retry))


# ------------------------------------------------------------------ middleware
@dataclass(frozen=True)
class EndpointLimit:
    limit: int
    window_seconds: int


DEFAULT_ENDPOINT_LIMITS: Dict[str, EndpointLimit] = {
    "/ingest": EndpointLimit(limit=10, window_seconds=3600),  # 10 req / hour / IP
    "/query": EndpointLimit(limit=60, window_seconds=60),     # 60 req / minute / IP
}


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class APIRateLimitMiddleware(BaseHTTPMiddleware):
    """app.add_middleware(APIRateLimitMiddleware)"""

    def __init__(self, app, rules: Optional[Dict[str, EndpointLimit]] = None, redis_client=None):
        super().__init__(app)
        self.rules = rules or DEFAULT_ENDPOINT_LIMITS
        self.limiter = SlidingWindowRateLimiter(redis_client)

    def _match(self, path: str) -> Optional[Tuple[str, EndpointLimit]]:
        clean = path.rstrip("/")
        for prefix, rule in self.rules.items():
            if clean == prefix or clean.endswith(prefix):
                return prefix, rule
        return None

    async def dispatch(self, request: Request, call_next):
        matched = self._match(request.url.path)
        if matched is None or request.method == "OPTIONS":
            return await call_next(request)

        name, rule = matched
        ip = client_ip(request)
        try:
            result = await self.limiter.check(f"rl:{name}:{ip}", rule.limit, rule.window_seconds)
        except RedisError:
            logger.exception("Rate limiter Redis error; allowing request")
            return await call_next(request)

        if not result.allowed:
            return JSONResponse(
                status_code=429,
                content={"error": "rate_limit_exceeded", "retry_after": result.retry_after},
                headers={
                    "Retry-After": str(result.retry_after),
                    "X-RateLimit-Limit": str(rule.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(rule.limit)
        response.headers["X-RateLimit-Remaining"] = str(result.remaining)
        return response
