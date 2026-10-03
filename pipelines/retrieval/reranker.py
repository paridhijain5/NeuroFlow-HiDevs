"""Step 4: cross-encoder reranking (LLM-scored, or a local sentence-transformers model)."""
from __future__ import annotations

import asyncio
import logging
import re

from .models import RetrievalResult

log = logging.getLogger(__name__)

PROMPT = ("Rate the relevance of this passage to the query on a scale of 0-10. "
          "Query: {query}. Passage: {passage}. Return only the number.")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def parse_score(text: str) -> float | None:
    m = _NUMBER.search(text or "")
    return None if m is None else max(0.0, min(10.0, float(m.group(0))))


def _order(candidates: list[RetrievalResult], scores: list[float | None], top_k: int | None):
    """Sort by rerank score desc; unscored items keep their RRF order after scored ones."""
    indexed = list(enumerate(zip(candidates, scores)))
    indexed.sort(key=lambda t: (t[1][1] is None, -(t[1][1] or 0.0), t[0]))
    out = []
    for _, (cand, sc) in indexed:
        out.append(cand.copy(rerank_score=sc, score=sc if sc is not None else 0.0))
    return out[:top_k] if top_k else out


class LLMReranker:
    """API-based: one 0-10 relevance rating per (query, passage) pair, scored in parallel."""

    def __init__(self, client, concurrency: int = 10, max_passage_chars: int = 6000):
        self.client, self.concurrency, self.max_chars = client, concurrency, max_passage_chars

    async def rerank(self, query: str, candidates: list[RetrievalResult],
                     top_k: int | None = None) -> list[RetrievalResult]:
        from providers.base import ChatMessage
        from providers.router import RoutingCriteria

        sem = asyncio.Semaphore(self.concurrency)   # stay under provider rate limits

        async def score(c: RetrievalResult) -> float | None:
            async with sem:
                try:
                    r = await self.client.chat(
                        [ChatMessage("user", PROMPT.format(query=query, passage=c.content[: self.max_chars]))],
                        RoutingCriteria(task_type="classification"), temperature=0, max_tokens=5)
                    return parse_score(r.content)
                except Exception as exc:
                    log.warning("Rerank scoring failed for %s: %s", c.chunk_id, exc)
                    return None

        scores = await asyncio.gather(*(score(c) for c in candidates))
        return _order(candidates, list(scores), top_k)


class LocalCrossEncoder:
    """Optional: sentence-transformers cross-encoder (faster and free, needs the model download)."""

    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
        self.model_name = model_name
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_name)
        return self._model

    async def rerank(self, query: str, candidates: list[RetrievalResult],
                     top_k: int | None = None) -> list[RetrievalResult]:
        def predict():
            return [float(s) for s in self._load().predict([(query, c.content) for c in candidates])]

        scores = await asyncio.to_thread(predict)
        return _order(candidates, scores, top_k)
