"""NeuroFlow production resilience layer (Task 10)."""
from .backpressure import (
    IngestQueueFull,
    check_ingest_backpressure,
    enforce_ingest_backpressure,
    ingest_queue_full_handler,
)
from .circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState, circuit_breaker
from .rate_limiter import (
    APIRateLimitMiddleware,
    PipelineRateLimiter,
    RateLimitExceeded,
    SlidingWindowRateLimiter,
    TokenBucket,
    llm_rate_limiter,
)
from .timeout_manager import OperationTimeoutError, TimeoutManager, timeout_manager


async def resilient_call(provider: str, task_type: str, make_coro, *,
                         pipeline_id: str = None, pipeline_rpm: int = None):
    """Global rate limit -> pipeline rate limit -> circuit breaker -> timeout.

        result = await resilient_call("openai", "chat_completion",
                                      lambda: provider.complete(messages))
    `make_coro` is a zero-arg callable so the coroutine is only created once allowed.
    """
    await llm_rate_limiter(provider).acquire()
    if pipeline_id and pipeline_rpm:
        await PipelineRateLimiter().acquire(pipeline_id, pipeline_rpm)
    async with circuit_breaker(provider):
        return await timeout_manager.run(task_type, make_coro())


__all__ = [
    "CircuitBreaker", "CircuitOpenError", "CircuitState", "circuit_breaker",
    "TokenBucket", "llm_rate_limiter", "PipelineRateLimiter", "RateLimitExceeded",
    "SlidingWindowRateLimiter", "APIRateLimitMiddleware",
    "IngestQueueFull", "check_ingest_backpressure", "enforce_ingest_backpressure",
    "ingest_queue_full_handler", "TimeoutManager", "OperationTimeoutError",
    "timeout_manager", "resilient_call",
]
