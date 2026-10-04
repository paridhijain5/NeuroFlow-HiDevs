"""Shared instrumentation for the retrieval entry point (query.py and the pipeline runner both use it)."""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from . import metrics, tracing


async def traced_retrieval(fn: Callable[[], Awaitable[Any]], *, pipeline_id, run_id=None,
                           default_strategy: str = "hybrid") -> Any:
    """Wraps your retrieval call in the `retrieval.pipeline` parent span and records the latency histogram.
    pipeline_id / run_id are attached to every span created inside (retrieval.dense, .sparse, ... too)."""
    token = tracing.set_context(pipeline_id=pipeline_id, run_id=run_id)
    try:
        with tracing.span(tracing.SPANS.RETRIEVAL, pipeline_id=pipeline_id, run_id=run_id) as sp:
            start = time.perf_counter()
            ctx = await fn()
            elapsed = time.perf_counter() - start
            strategy = getattr(ctx, "strategy", default_strategy)
            sp.set_attributes({"chunk_count": len(ctx.chunks), "latency_ms": int(elapsed * 1000),
                               "strategy": strategy})
            metrics.observe_retrieval(strategy, elapsed)
            return ctx
    finally:
        tracing.reset_context(token)
