"""Streaming generation with pipeline_runs logging, async eval enqueue, tracing and Prometheus metrics."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator

from backend.monitoring import metrics, tracing
from backend.monitoring.tracing import SPANS

from .citations import parse_citations
from .prompt_builder import build_messages

_background: set[asyncio.Task] = set()


def _fire_and_forget(coro) -> None:
    """Schedule without awaiting; keep a reference so it isn't GC'd."""
    t = asyncio.create_task(coro)
    _background.add(t)
    t.add_done_callback(_background.discard)


async def _enqueue_eval(redis, run_id: str, trace_carrier: dict) -> None:
    try:   # the carrier lets the eval worker continue THIS trace (retrieval -> generation -> evaluation)
        await redis.lpush("eval_jobs", json.dumps({"run_id": run_id, "trace": trace_carrier}))
    except Exception:
        pass  # evaluation must never break generation


async def stream_generation(
    *, run_id: str, query: str, ctx: Any, query_type: str,
    provider: Any, db: Any, redis: Any,
    pipeline_id: str | None = None,
    pipeline_version: int | None = None,
    retrieval_latency_ms: int | None = None,
    gen_params: dict | None = None,
    auto_evaluate: bool = True,
    guard: Any = None,
    provider_name: str = "openai",
    task_type: str = "rag_generation",
) -> AsyncIterator[dict]:
    """
    Yields SSE payload dicts: {"type": "token"|"done"|"error", ...}.
    Spans: generation.pipeline > prompt_build, log_run (x2), llm_call, citation_parse   (all carry pipeline_id/run_id)
    Metrics: queries_total, llm_calls_total, generation_latency, llm_cost.
    """
    common = {"pipeline_id": pipeline_id, "run_id": run_id}
    root = tracing.start_span(SPANS.GENERATION, **common, query_type=query_type, chunk_count=len(ctx.chunks),
                              pipeline_version=pipeline_version)
    parent = tracing.context_of(root)
    prov_name = getattr(provider, "name", provider_name)
    model = getattr(provider, "model", "unknown")
    error: BaseException | None = None
    try:
        # 1. prompt assembly
        with tracing.span(SPANS.PROMPT_BUILD, parent=parent, **common, chunk_count=len(ctx.chunks)) as sp:
            messages = build_messages(query, ctx.text, query_type)
            sp.set_attribute("prompt_chars", sum(len(m["content"]) for m in messages))

        # 2. log the full prompt BEFORE calling the LLM
        with tracing.span(SPANS.LOG_RUN, parent=parent, **common, phase="start"):
            await db.execute(
                """INSERT INTO pipeline_runs (id, query, prompt, status, pipeline_id, pipeline_version, retrieval_latency_ms)
                   VALUES ($1, $2, $3, 'running', $4, $5, $6)
                   ON CONFLICT (id) DO UPDATE SET prompt = EXCLUDED.prompt, status = 'running',
                       pipeline_id = EXCLUDED.pipeline_id, pipeline_version = EXCLUDED.pipeline_version,
                       retrieval_latency_ms = EXCLUDED.retrieval_latency_ms""",
                run_id, query, json.dumps(messages), pipeline_id, pipeline_version, retrieval_latency_ms,
            )

        # 3. stream tokens (span stays open across yields -> manual span)
        llm = tracing.start_span(SPANS.LLM_CALL, parent=parent, **common, provider=prov_name, model=model,
                                 task_type=task_type)
        start = time.perf_counter()
        parts: list[str] = []
        first_token_ms = None
        try:
            stream = provider.stream(messages, **(gen_params or {}))
            if guard is not None:       # circuit breaker + global rate limit + explicit chat_completion timeout
                stream = guard.stream(prov_name, "chat_completion", stream)
            async for delta in stream:
                if first_token_ms is None:
                    first_token_ms = int((time.perf_counter() - start) * 1000)
                parts.append(delta)
                yield {"type": "token", "delta": delta}
        except Exception as exc:
            error = exc
            tracing.end_span(llm, exc)
            metrics.record_llm_call(prov_name, model, task_type)
            metrics.record_query(pipeline_id, "error")
            await db.execute(
                "UPDATE pipeline_runs SET status='failed', metadata = jsonb_build_object('error', $2::text) WHERE id=$1",
                run_id, str(exc),
            )
            yield {"type": "error", "message": "generation failed", "code": type(exc).__name__}
            return
        except BaseException as exc:      # GeneratorExit / cancellation: close the span, don't count it
            tracing.end_span(llm, exc)
            raise

        full = "".join(parts)
        usage = getattr(provider, "last_usage", None) or {}
        model = usage.get("model") or model
        input_tokens = usage.get("input_tokens") or sum(len(m["content"]) for m in messages) // 4
        output_tokens = usage.get("output_tokens") or len(full) // 4
        latency_s = time.perf_counter() - start
        latency_ms = int(latency_s * 1000)
        cost = metrics.record_llm_call(prov_name, model, task_type, latency_s=latency_s,
                                       input_tokens=input_tokens, output_tokens=output_tokens)
        llm.set_attributes({"model": model, "input_tokens": input_tokens, "output_tokens": output_tokens,
                            "latency_ms": latency_ms, "cost_usd": cost or 0.0,
                            **({"first_token_ms": first_token_ms} if first_token_ms is not None else {})})
        tracing.end_span(llm)

        # 4. parse citations
        with tracing.span(SPANS.CITATION_PARSE, parent=parent, **common) as sp:
            citations, invalid = parse_citations(full, ctx.chunks)
            sp.set_attributes({"citation_count": len(citations), "invalid_citation_count": len(invalid)})

        # 5. update pipeline_runs
        with tracing.span(SPANS.LOG_RUN, parent=parent, **common, phase="complete"):
            await db.execute(
                """UPDATE pipeline_runs
                   SET generation=$2, input_tokens=$3, output_tokens=$4, model_used=$5,
                       latency_ms=$6, status='complete',
                       metadata = COALESCE(metadata, '{}'::jsonb) || $7::jsonb
                   WHERE id=$1""",
                run_id, full, input_tokens, output_tokens, model, latency_ms,
                json.dumps({"invalid_citations": invalid, "chunks": [c.content for c in ctx.chunks]}),
            )
        metrics.record_query(pipeline_id, "ok")
        root.set_attributes({"model": model, "input_tokens": input_tokens, "output_tokens": output_tokens,
                             "latency_ms": latency_ms, "citation_count": len(citations)})

        # 6. enqueue eval asynchronously - do NOT await; carry the trace context into the job
        if auto_evaluate:
            _fire_and_forget(_enqueue_eval(redis, run_id, tracing.inject_trace_context(parent)))

        yield {"type": "done", "run_id": run_id, "citations": [c.to_dict() for c in citations],
               "invalid_citations": invalid}
    except Exception as exc:      # failures outside the LLM call (db, prompt build, ...)
        error = exc
        metrics.record_query(pipeline_id, "error")
        raise
    finally:
        tracing.end_span(root, error)
