"""IngestionPipeline: extract -> chunk -> embed -> store, with tracing and structured logs."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time

from opentelemetry import trace

from .chunker import chunk_pages
from .extractors import (ExtractionError, extract_csv, extract_docx, extract_image,
                         extract_pdf, extract_url)

log = logging.getLogger("ingestion")
tracer = trace.get_tracer("neuroflow.ingestion")

EMBED_BATCH = 100
INSERT_CHUNK = (
    "INSERT INTO chunks (id, document_id, content, embedding, chunk_index, token_count, metadata) "
    "VALUES ($1, $2, $3, $4::vector, $5, $6, $7::jsonb)"
)


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


class IngestionPipeline:
    def __init__(self, pool, client):
        self.pool = pool
        self.client = client  # NeuroFlowClient (chat for vision, embed for vectors)

    async def _extract(self, source_type: str, source: str):
        if source_type == "pdf":
            return await asyncio.to_thread(extract_pdf, source)
        if source_type == "docx":
            return await asyncio.to_thread(extract_docx, source)
        if source_type == "csv":
            return await asyncio.to_thread(extract_csv, source)
        if source_type == "image":
            return await extract_image(source, self.client)
        if source_type == "url":
            return await extract_url(source)
        raise ExtractionError(f"Unsupported source_type '{source_type}'")

    async def run(self, document_id: str, source: str, source_type: str) -> dict:
        started = time.perf_counter()
        await self.pool.execute("UPDATE documents SET status='processing' WHERE id=$1", document_id)
        with tracer.start_as_current_span("ingestion.process") as span:
            span.set_attribute("document_id", document_id)
            span.set_attribute("source_type", source_type)
            try:
                pages = await self._extract(source_type, source)
                if not pages:
                    raise ExtractionError("No content could be extracted")
                page_count = max(p.page_number for p in pages)

                calls = 0

                async def embed_fn(texts: list[str]):
                    nonlocal calls
                    calls += math.ceil(len(texts) / EMBED_BATCH)
                    return await self.client.embed(texts)

                chunks, strategy = await chunk_pages(pages, source_type, embed_fn)
                if not chunks:
                    raise ExtractionError("Chunking produced no chunks")
                vectors = await embed_fn([c.content for c in chunks])

                tokens = sum(c.token_count for c in chunks)
                info = {"page_count": page_count, "chunking_strategy": strategy,
                        "embedding_calls": calls, "tokens": tokens}
                # url/image extractors attach useful document-level metadata
                for key in ("title", "author", "canonical_url", "publish_date", "source_url"):
                    if pages[0].metadata.get(key):
                        info[key] = pages[0].metadata[key]

                rows = [(c.id, document_id, c.content, _vector_literal(v), c.chunk_index,
                         c.token_count, json.dumps(c.metadata, default=str))
                        for c, v in zip(chunks, vectors)]
                async with self.pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.execute("DELETE FROM chunks WHERE document_id=$1", document_id)
                        await conn.executemany(INSERT_CHUNK, rows)
                        await conn.execute(
                            "UPDATE documents SET status='complete', chunk_count=$2, "
                            "metadata = metadata || $3::jsonb WHERE id=$1",
                            document_id, len(rows), json.dumps(info))

                span.set_attribute("page_count", page_count)
                span.set_attribute("chunk_count", len(rows))
                span.set_attribute("embedding_calls", calls)
                duration_ms = round((time.perf_counter() - started) * 1000)
                log.info(json.dumps({"event": "ingestion_complete", "document_id": document_id,
                                     "duration_ms": duration_ms, "chunks": len(rows), "tokens": tokens}))
                return {"status": "complete", "chunks": len(rows), **info}
            except Exception as exc:
                span.record_exception(exc)
                await self.pool.execute(
                    "UPDATE documents SET status='failed', metadata = metadata || $2::jsonb WHERE id=$1",
                    document_id, json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
                log.error(json.dumps({"event": "ingestion_failed", "document_id": document_id,
                                      "error": str(exc)}))
                return {"status": "failed", "error": str(exc)}
