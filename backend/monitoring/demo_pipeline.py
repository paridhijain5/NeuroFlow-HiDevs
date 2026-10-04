"""Reference implementation of the span pattern for the parts of NeuroFlow I can't see (ingestion, retrieval stages)
+ in-memory fakes used by the demo app and the self-test. Copy the `with span(...)` blocks into your real modules."""
from __future__ import annotations

import asyncio, json, uuid
from types import SimpleNamespace

from opentelemetry import context as otel_context

from backend.monitoring import metrics, tracing
from backend.monitoring.instrument import traced_retrieval
from backend.monitoring.tracing import SPANS, span
from pipelines.generation.generator import stream_generation


# ------------------------------------------------------------------ ingestion (pattern for your ingestion module)
async def ingest_document(source_type: str, pipeline_id: str, text: str) -> int:
    token = tracing.set_context(pipeline_id=pipeline_id)         # every span below gets pipeline_id automatically
    try:
        with span(SPANS.INGEST, source_type=source_type, char_count=len(text)) as root:
            with span(SPANS.EXTRACT.format(source_type=source_type), source_type=source_type, char_count=len(text)):
                await asyncio.sleep(0.001)
            with span(SPANS.CHUNK, strategy="recursive") as sp:
                chunks = [text[i:i + 200] for i in range(0, max(len(text), 1), 200)]
                sp.set_attribute("chunk_count", len(chunks))
            with span(SPANS.EMBED, model="text-embedding-3-small") as sp:
                await asyncio.sleep(0.001)
                sp.set_attributes({"token_count": sum(len(c) // 4 for c in chunks), "chunk_count": len(chunks)})
            with span(SPANS.WRITE_DB, rows=len(chunks)):
                await asyncio.sleep(0.001)
            metrics.record_ingested_doc(source_type)             # updated where the operation occurs
            root.set_attribute("chunk_count", len(chunks))
            return len(chunks)
    finally:
        tracing.reset_context(token)


# ------------------------------------------------------------------ retrieval stages (pattern)
async def retrieve_with_stages(pipeline_id: str, query: str):
    async def stage(name, **attrs):
        with span(name, **attrs):
            await asyncio.sleep(0.001)

    await asyncio.gather(stage(SPANS.DENSE, k=30), stage(SPANS.SPARSE, k=20), stage(SPANS.METADATA, filters=0))
    await stage(SPANS.FUSION, method="rrf")
    await stage(SPANS.RERANK, model="cross-encoder", top_k=2)
    with span(SPANS.ASSEMBLE) as sp:
        chunks = [SimpleNamespace(chunk_id=uuid.uuid4(), document_name="doc.pdf", page_number=1,
                                  content="RAG grounds answers in retrieved text. " * 3),
                  SimpleNamespace(chunk_id=uuid.uuid4(), document_name="paper.pdf", page_number=None,
                                  content="Streaming reduces perceived latency. " * 3)]
        sp.set_attribute("chunk_count", len(chunks))
    text = "\n\n".join(f"[Source {i}] {c.content}" for i, c in enumerate(chunks, 1))
    return SimpleNamespace(text=text, chunks=chunks, query_type="factual", sources=["doc.pdf", "paper.pdf"])


# ------------------------------------------------------------------ fakes
class FakeProvider:
    name = "openai"
    model = "gpt-4o-mini"

    def __init__(self, fail: bool = False, delay: float = 0.002):
        self.fail, self.delay = fail, delay
        self.last_usage = {"input_tokens": 1200, "output_tokens": 180, "model": "gpt-4o-mini"}

    async def stream(self, messages, **kw):
        if self.fail:
            raise RuntimeError("provider 500")
        for tok in ["Based on the sources, ", "RAG grounds answers ", "in retrieved text [Source 1]. ",
                    "Streaming helps latency [Source 2]."]:
            await asyncio.sleep(self.delay)
            yield tok


class FakeDB:
    """Captures what the generator writes so EvaluationJudge.evaluate_run() can read it back."""

    def __init__(self):
        self.runs: dict[str, dict] = {}

    async def execute(self, sql, *args):
        if sql.lstrip().startswith("INSERT INTO pipeline_runs"):
            self.runs[args[0]] = {"query": args[1], "pipeline_id": args[3], "generation": None, "metadata": "{}"}
        elif "SET generation" in sql:
            self.runs[args[0]].update(generation=args[1], metadata=args[6])

    async def fetchrow(self, sql, run_id):
        return self.runs.get(run_id)


async def run_query_flow(redis, db, provider, pipeline_id: str, query: str, auto_evaluate: bool = True) -> dict:
    """One request: demo.request > retrieval.pipeline (+stages) and generation.pipeline (+children);
    the eval job carries the trace context to the (simulated) worker."""
    run_id = str(uuid.uuid4())
    events = []
    with span("demo.request", route="/demo/query"):
        ctx = await traced_retrieval(lambda: retrieve_with_stages(pipeline_id, query),
                                     pipeline_id=pipeline_id, run_id=run_id)
        async for ev in stream_generation(run_id=run_id, query=query, ctx=ctx, query_type=ctx.query_type,
                                          provider=provider, db=db, redis=redis, pipeline_id=pipeline_id,
                                          auto_evaluate=auto_evaluate):
            events.append(ev)
    return {"run_id": run_id, "events": events}


async def run_eval_worker_once(redis, judge) -> object | None:
    """What evaluation/worker.py does: pop a job, continue the API's trace, evaluate."""
    for _ in range(50):                                   # the enqueue is fire-and-forget
        raw = await redis.rpop("eval_jobs")
        if raw:
            break
        await asyncio.sleep(0.01)
    else:
        return None
    payload = json.loads(raw)
    token = otel_context.attach(tracing.extract_trace_context(payload.get("trace")))
    try:
        return await judge.evaluate_run(payload["run_id"])
    finally:
        otel_context.detach(token)
