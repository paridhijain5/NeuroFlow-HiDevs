"""Task 10 tests. Run: pytest backend/tests/test_resilience.py -v
Needs: pytest pytest-asyncio fakeredis lupa  (fakeredis needs lupa for Lua scripts)
"""
import asyncio

import fakeredis.aioredis
import pytest
import pytest_asyncio

from backend.resilience import backpressure
from backend.resilience.circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState
from backend.resilience.health import compute_status
from backend.resilience.rate_limiter import PipelineRateLimiter, RateLimitExceeded, SlidingWindowRateLimiter, TokenBucket
from backend.resilience.timeout_manager import OperationTimeoutError, TimeoutManager

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.flushall()
    await r.aclose()


async def _fail(cb):
    with pytest.raises(RuntimeError):
        async with cb:
            raise RuntimeError("provider down")


async def _ok(cb):
    async with cb:
        return "ok"


# ------------------------------------------------------------ circuit breaker
async def test_opens_after_5_failures_and_6th_call_raises(redis):
    cb = CircuitBreaker("t1", failure_threshold=5, redis_client=redis)
    for _ in range(5):
        await _fail(cb)
    assert await cb.get_state() == CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        async with cb:
            pytest.fail("call should not execute while open")


async def test_success_resets_consecutive_failures(redis):
    cb = CircuitBreaker("t2", failure_threshold=5, redis_client=redis)
    for _ in range(4):
        await _fail(cb)
    await _ok(cb)
    await _fail(cb)
    assert await cb.get_state() == CircuitState.CLOSED


async def test_half_open_allows_exactly_max_calls(redis):
    cb = CircuitBreaker("t3", failure_threshold=1, recovery_timeout=0, half_open_max_calls=3,
                        redis_client=redis)
    await _fail(cb)
    admitted = rejected = 0
    for _ in range(5):
        try:
            await cb._before_call()   # admission only, no result recorded
            admitted += 1
        except CircuitOpenError:
            rejected += 1
    assert (admitted, rejected) == (3, 2)


async def test_half_open_success_closes_and_failure_reopens(redis):
    cb = CircuitBreaker("t4", failure_threshold=1, recovery_timeout=0, redis_client=redis)
    await _fail(cb)
    assert await _ok(cb) == "ok"
    assert await cb.get_state() == CircuitState.CLOSED

    await _fail(cb)                   # opens again
    await _fail(cb)                   # half-open trial fails
    assert await cb.get_state() == CircuitState.OPEN


async def test_state_shared_across_instances(redis):
    a = CircuitBreaker("t5", failure_threshold=2, redis_client=redis)
    b = CircuitBreaker("t5", failure_threshold=2, redis_client=redis)  # "restarted" worker
    await _fail(a)
    await _fail(a)
    with pytest.raises(CircuitOpenError):
        async with b:
            pass
    status = await b.status()
    assert status["state"] == "open" and "opened_at" in status


# ------------------------------------------------------------- rate limiting
async def test_token_bucket_70_requests_against_60_rpm(redis):
    bucket = TokenBucket("rpb:pipeline:p1:tokens", capacity=60, refill_rate=1.0, redis_client=redis)
    results = [(await bucket.try_acquire())[0] for _ in range(70)]
    assert results.count(True) == 60
    assert results.count(False) == 10


async def test_token_bucket_state_persists_across_instances(redis):
    a = TokenBucket("rpb:x:tokens", 3, 0.001, redis_client=redis)
    for _ in range(3):
        await a.try_acquire()
    b = TokenBucket("rpb:x:tokens", 3, 0.001, redis_client=redis)
    assert (await b.try_acquire())[0] is False


async def test_token_bucket_acquire_waits_then_succeeds(redis):
    bucket = TokenBucket("rpb:w:tokens", capacity=1, refill_rate=20, redis_client=redis)
    await bucket.acquire()
    await bucket.acquire(max_wait=2)   # waits ~50ms for a refill


async def test_pipeline_limit(redis):
    limiter = PipelineRateLimiter(redis)
    for _ in range(60):
        await limiter.check("pipe", 60)
    with pytest.raises(RateLimitExceeded):
        await limiter.check("pipe", 60)


async def test_sliding_window_10_per_hour(redis):
    sw = SlidingWindowRateLimiter(redis)
    res = [await sw.check("rl:/ingest:1.2.3.4", 10, 3600) for _ in range(12)]
    assert [r.allowed for r in res] == [True] * 10 + [False] * 2
    assert res[-1].retry_after > 0
    other_ip = await sw.check("rl:/ingest:5.6.7.8", 10, 3600)
    assert other_ip.allowed


# -------------------------------------------------------------- backpressure
async def test_backpressure_thresholds(redis):
    async def at_depth(n):
        await redis.delete(backpressure.QUEUE_KEY)
        if n:
            await redis.rpush(backpressure.QUEUE_KEY, *range(n))
        return await backpressure.check_ingest_backpressure(redis)

    d = await at_depth(10)
    assert d.status_code == 200 and d.body is None
    d = await at_depth(75)
    assert d.status_code == 202 and d.body["warning"] == "high_queue_depth"
    assert d.body["estimated_wait_minutes"] >= 1
    d = await at_depth(101)
    assert d.status_code == 503
    assert d.body == {"error": "ingestion_queue_full", "queue_depth": 101, "retry_after": 30}


# ------------------------------------------------------------------ timeouts
async def test_timeout_raises_and_counts(redis, monkeypatch):
    monkeypatch.setitem(__import__("backend.resilience.timeout_manager", fromlist=["x"]).TIMEOUTS,
                        "url_fetch", 0.05)
    tm = TimeoutManager(redis, adaptive=False)
    with pytest.raises(TimeoutError) as exc:
        await tm.run("url_fetch", asyncio.sleep(1))
    assert isinstance(exc.value, OperationTimeoutError)
    assert await redis.get("timeouts:url_fetch") == "1"


async def test_unknown_task_type(redis):
    with pytest.raises(ValueError):
        await TimeoutManager(redis).get_timeout("nope")


async def test_adaptive_timeout_uses_p95_x_1_5(redis):
    tm = TimeoutManager(redis, adaptive=True)
    async def quick():
        return 1
    for _ in range(50):
        await tm.run("embedding", quick())
    t = await tm.get_timeout("embedding")
    assert 5.0 <= t <= 30.0 and t == 5.0   # tiny p95 -> clamped to 0.5 * base(10)


# -------------------------------------------------------------------- health
async def test_health_status_logic():
    ok = {"postgres": {"status": "ok"}, "redis": {"status": "ok"}, "mlflow": {"status": "ok"}}
    closed = {"openai": {"state": "closed"}}
    assert compute_status(ok, closed) == "ok"
    assert compute_status(ok, {**closed, "anthropic": {"state": "open"}}) == "degraded"
    assert compute_status({**ok, "redis": {"status": "error"}}, closed) == "critical"
