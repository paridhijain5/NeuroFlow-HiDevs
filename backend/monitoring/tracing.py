"""OpenTelemetry helpers: setup (OTLP -> Jaeger), span helpers, automatic pipeline_id/run_id attributes,
and trace-context propagation through the Redis queues (so ingest/eval workers join the API's trace)."""
from __future__ import annotations

import functools, inspect, os
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Callable

from opentelemetry import propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.trace import Status, StatusCode

TRACER_NAME = "neuroflow"
_ctx_attrs: ContextVar[dict | None] = ContextVar("neuroflow_span_attrs", default=None)
_installed: TracerProvider | None = None


class SPANS:
    """Required span names (Task 42)."""
    INGEST = "ingestion.process"
    EXTRACT = "ingestion.extract.{source_type}"
    CHUNK, EMBED, WRITE_DB = "ingestion.chunk", "ingestion.embed", "ingestion.write_db"
    RETRIEVAL = "retrieval.pipeline"
    DENSE, SPARSE, METADATA = "retrieval.dense", "retrieval.sparse", "retrieval.metadata"
    FUSION, RERANK, ASSEMBLE = "retrieval.fusion", "retrieval.rerank", "retrieval.assemble"
    GENERATION = "generation.pipeline"
    PROMPT_BUILD, LLM_CALL = "generation.prompt_build", "generation.llm_call"
    CITATION_PARSE, LOG_RUN = "generation.citation_parse", "generation.log_run"
    JUDGE = "evaluation.judge"
    FAITHFULNESS, ANSWER_RELEVANCE = "evaluation.faithfulness", "evaluation.answer_relevance"
    CONTEXT_PRECISION, CONTEXT_RECALL = "evaluation.context_precision", "evaluation.context_recall"


def _clean(attrs: dict) -> dict:
    out = {}
    for k, v in attrs.items():
        if v is None:
            continue
        out[k] = v if isinstance(v, (str, bool, int, float)) else str(v)
    return out


# ---- pipeline_id / run_id propagate to EVERY span started inside the block (via a SpanProcessor)
def set_context(**attrs: Any) -> Token:
    merged = dict(_ctx_attrs.get() or {})
    merged.update(_clean(attrs))
    return _ctx_attrs.set(merged)


def reset_context(token: Token) -> None:
    try:
        _ctx_attrs.reset(token)
    except ValueError:      # reset from a different Context (e.g. async-generator finalisation) - harmless
        pass


class ContextAttributeProcessor(SpanProcessor):
    def on_start(self, span, parent_context=None):
        have = span.attributes or {}
        for k, v in (_ctx_attrs.get() or {}).items():
            if k not in have:
                span.set_attribute(k, v)


def setup_tracing(service_name: str = "neuroflow-api", otlp_endpoint: str | None = None, *, exporter=None,
                  simple: bool = False) -> TracerProvider:
    """otlp_endpoint e.g. http://jaeger:4317 (Jaeger all-in-one speaks OTLP). Without an endpoint/exporter spans are
    still created (and propagated) but not exported. Idempotent: the first call wins (OTel allows one global provider)."""
    global _installed
    if _installed is not None:
        return _installed
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(ContextAttributeProcessor())
    endpoint = otlp_endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if exporter is None and endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter) if simple else BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _installed = provider
    return provider


def tracer():
    return trace.get_tracer(TRACER_NAME)


@contextmanager
def span(name: str, *, parent=None, **attrs):
    """with span("retrieval.dense", chunk_count=n) as s: ...   (parent=None -> current span)"""
    with tracer().start_as_current_span(name, context=parent, attributes=_clean(attrs)) as s:
        yield s


def start_span(name: str, *, parent=None, **attrs):
    """Manual span (NOT made current) - for spans that stay open across `yield`s. Pair with end_span()."""
    return tracer().start_span(name, context=parent, attributes=_clean(attrs))


def context_of(s):
    return trace.set_span_in_context(s)


def end_span(s, error: BaseException | None = None) -> None:
    if error is not None:
        s.record_exception(error)
        s.set_status(Status(StatusCode.ERROR, str(error)[:200]))
    s.end()


def traced(name: str, attrs: Callable[..., dict] | None = None):
    """Decorator for sync/async functions. attrs(*args, **kwargs) -> dict of span attributes."""
    def deco(fn):
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def aw(*a, **kw):
                with span(name, **(attrs(*a, **kw) if attrs else {})):
                    return await fn(*a, **kw)
            return aw

        @functools.wraps(fn)
        def w(*a, **kw):
            with span(name, **(attrs(*a, **kw) if attrs else {})):
                return fn(*a, **kw)
        return w
    return deco


# ---- context propagation through queues (W3C traceparent)
def inject_trace_context(context=None) -> dict:
    carrier: dict = {}
    propagate.inject(carrier, context=context)
    return carrier


def extract_trace_context(carrier: dict | None):
    return propagate.extract(carrier or {})
