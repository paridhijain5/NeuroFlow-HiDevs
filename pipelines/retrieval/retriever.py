"""Step 2: parallel dense / sparse / metadata retrieval."""
from __future__ import annotations

import asyncio
import json
import logging
import re

from .fusion import reciprocal_rank_fusion
from .models import RetrievalResult
from .query_processor import ProcessedQuery

log = logging.getLogger(__name__)

_COLS = "c.id, c.document_id, c.content, c.metadata, d.filename"

DENSE_SQL = (f"SELECT {_COLS}, 1 - (c.embedding <=> $1::vector) AS score "
             "FROM chunks c JOIN documents d ON d.id = c.document_id "
             "WHERE c.embedding IS NOT NULL ORDER BY c.embedding <=> $1::vector LIMIT $2")

SPARSE_SQL = (f"SELECT {_COLS}, ts_rank_cd(to_tsvector('english', c.content), "
              "plainto_tsquery('english', $1)) AS score "
              "FROM chunks c JOIN documents d ON d.id = c.document_id "
              "WHERE to_tsvector('english', c.content) @@ plainto_tsquery('english', $1) "
              "ORDER BY score DESC LIMIT $2")

# plainto_tsquery ANDs every term, so long natural-language questions often match nothing.
# Fallback: OR the individual terms and let ts_rank_cd order them.
SPARSE_OR_SQL = (f"SELECT {_COLS}, ts_rank_cd(to_tsvector('english', c.content), "
                 "to_tsquery('english', $1)) AS score "
                 "FROM chunks c JOIN documents d ON d.id = c.document_id "
                 "WHERE to_tsvector('english', c.content) @@ to_tsquery('english', $1) "
                 "ORDER BY score DESC LIMIT $2")

METADATA_SQL = (f"SELECT {_COLS}, 1 - (c.embedding <=> $2::vector) AS score "
                "FROM chunks c JOIN documents d ON d.id = c.document_id "
                "WHERE c.metadata @> $1::jsonb ORDER BY c.embedding <=> $2::vector LIMIT $3")


def vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def or_tsquery(text: str) -> str:
    words = dict.fromkeys(w.lower() for w in re.findall(r"[A-Za-z0-9]{3,}", text))
    return " | ".join(words)


class Retriever:
    def __init__(self, pool, client, rrf_k: int = 60, ef_search: int = 100):
        self.pool, self.client, self.rrf_k, self.ef_search = pool, client, rrf_k, ef_search

    async def retrieve(self, query: ProcessedQuery | str, k: int = 20, use_hyde: bool = False,
                       fuse: bool = True):
        """Run the three strategies in parallel; return the RRF-fused list (or the raw lists)."""
        pq = ProcessedQuery(original=query) if isinstance(query, str) else query
        # Dense queries: the original (or its HyDE passage) plus every expansion
        texts = [pq.hyde_text if (use_hyde and pq.hyde_text) else pq.original, *pq.expansions]
        vectors = await self.client.embed(texts)
        results = await asyncio.gather(
            self._dense_retrieval(vectors, k),
            self._sparse_retrieval(pq, k),
            self._metadata_retrieval(pq, vectors[0], k),
        )
        lists = [lst for group in results for lst in group]   # each strategy yields >= 1 ranked list
        return self._fuse(lists) if fuse else lists

    # ---- strategies ---------------------------------------------------------------
    async def _dense_retrieval(self, vectors: list[list[float]], k: int) -> list[list[RetrievalResult]]:
        async def one(vec):
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(f"SET LOCAL hnsw.ef_search = {int(max(self.ef_search, k))}")
                    rows = await conn.fetch(DENSE_SQL, vector_literal(vec), k)
            return [RetrievalResult.from_row(r, "dense") for r in rows]

        return list(await asyncio.gather(*(one(v) for v in vectors)))

    async def _sparse_retrieval(self, pq: ProcessedQuery, k: int) -> list[list[RetrievalResult]]:
        async def one(text):
            rows = await self.pool.fetch(SPARSE_SQL, text, k)
            if not rows:
                q = or_tsquery(text)
                rows = await self.pool.fetch(SPARSE_OR_SQL, q, k) if q else []
            return [RetrievalResult.from_row(r, "sparse") for r in rows]

        return list(await asyncio.gather(*(one(t) for t in pq.all_queries)))

    async def _metadata_retrieval(self, pq: ProcessedQuery, vector: list[float], k: int) -> list[list[RetrievalResult]]:
        if not pq.filters:
            return []
        rows = await self.pool.fetch(METADATA_SQL, json.dumps(pq.filters), vector_literal(vector), k)
        return [[RetrievalResult.from_row(r, "metadata") for r in rows]]

    def _fuse(self, lists: list[list[RetrievalResult]]) -> list[RetrievalResult]:
        return reciprocal_rank_fusion([lst for lst in lists if lst], k=self.rrf_k)

    async def dense_only(self, query: str, k: int = 10) -> list[RetrievalResult]:
        """Naive top-k cosine baseline used by the evaluation."""
        vectors = await self.client.embed([query])
        return (await self._dense_retrieval(vectors, k))[0]
