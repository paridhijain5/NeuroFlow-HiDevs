"""Streaming generation with pipeline_runs logging and async eval enqueue."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator

from .citations import parse_citations
from .prompt_builder import build_messages

_background: set[asyncio.Task] = set()


def _fire_and_forget(coro) -> None:
    """Schedule without awaiting; keep a reference so it isn't GC'd."""
    t = asyncio.create_task(coro)
    _background.add(t)
    t.add_done_callback(_background.discard)


async def _enqueue_eval(redis, run_id: str) -> None:
    try:
        await redis.lpush("eval_jobs", json.dumps({"run_id": run_id}))  # Task 37 worker consumes this
    except Exception:
        pass  # evaluation must never break generation


async def stream_generation(
    *, run_id: str, query: str, ctx: Any, query_type: str,
    provider: Any, db: Any, redis: Any,
    pipeline_id: str | None = None,          # Task 38: which named pipeline ...
    pipeline_version: int | None = None,     # ... and which version produced this run
    retrieval_latency_ms: int | None = None,
    gen_params: dict | None = None,          # e.g. {"temperature": 0.2}; ADAPT provider.stream to accept them
    auto_evaluate: bool = True,
) -> AsyncIterator[dict]:
    """
    Yields SSE payload dicts: {"type": "token"|"done"|"error", ...}.
    ADAPT: ctx needs .text (formatted context) and .chunks.
    ADAPT: provider.stream(messages, **gen_params) yields str deltas; optional provider.last_usage
           = {"input_tokens", "output_tokens", "model"}.
    ADAPT: db is an asyncpg pool.
    """
    messages = build_messages(query, ctx.text, query_type)

    # 1. Log the full prompt (+ pipeline version) BEFORE calling the LLM
    await db.execute(
        """INSERT INTO pipeline_runs (id, query, prompt, status, pipeline_id, pipeline_version, retrieval_latency_ms)
           VALUES ($1, $2, $3, 'running', $4, $5, $6)
           ON CONFLICT (id) DO UPDATE SET prompt = EXCLUDED.prompt, status = 'running',
               pipeline_id = EXCLUDED.pipeline_id, pipeline_version = EXCLUDED.pipeline_version,
               retrieval_latency_ms = EXCLUDED.retrieval_latency_ms""",
        run_id, query, json.dumps(messages), pipeline_id, pipeline_version, retrieval_latency_ms,
    )

    start = time.perf_counter()
    parts: list[str] = []
    try:
        # 2 + 3. Stream tokens, accumulate full response
        async for delta in provider.stream(messages, **(gen_params or {})):
            parts.append(delta)
            yield {"type": "token", "delta": delta}
    except Exception as exc:
        await db.execute(
            "UPDATE pipeline_runs SET status='failed', metadata = jsonb_build_object('error', $2::text) WHERE id=$1",
            run_id, str(exc),
        )
        yield {"type": "error", "message": "generation failed"}
        return

    full = "".join(parts)

    # 4. Parse citations after stream completes
    citations, invalid = parse_citations(full, ctx.chunks)

    # 5. Update pipeline_runs
    usage = getattr(provider, "last_usage", None) or {}
    input_tokens = usage.get("input_tokens") or sum(len(m["content"]) for m in messages) // 4
    output_tokens = usage.get("output_tokens") or len(full) // 4
    latency_ms = int((time.perf_counter() - start) * 1000)

    await db.execute(
        """UPDATE pipeline_runs
           SET generation=$2, input_tokens=$3, output_tokens=$4, model_used=$5,
               latency_ms=$6, status='complete',
               metadata = COALESCE(metadata, '{}'::jsonb) || $7::jsonb
           WHERE id=$1""",
        run_id, full, input_tokens, output_tokens,
        usage.get("model") or getattr(provider, "model", "unknown"),
        latency_ms,
        json.dumps({"invalid_citations": invalid,
                    "chunks": [c.content for c in ctx.chunks]}),   # Task 37 reads these
    )

    # 6. Enqueue eval asynchronously - do NOT await
    if auto_evaluate:
        _fire_and_forget(_enqueue_eval(redis, run_id))

    yield {
        "type": "done",
        "run_id": run_id,
        "citations": [c.to_dict() for c in citations],
        "invalid_citations": invalid,
    }
