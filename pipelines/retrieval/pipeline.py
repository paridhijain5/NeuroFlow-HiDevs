"""RetrievalPipeline: query processing -> parallel retrieval -> RRF -> rerank -> context assembly."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from opentelemetry import trace

from .context_assembler import ContextAssembler, AssembledContext
from .models import RetrievalResult
from .query_processor import ProcessedQuery, QueryProcessor
from .reranker import LLMReranker
from .retriever import Retriever

log = logging.getLogger(__name__)
tracer = trace.get_tracer("neuroflow.retrieval")

RETRIEVE_K = 20        # per retrieval list
RERANK_CANDIDATES = 40  # top fused results sent to the cross-encoder


@dataclass
class Stages:
    query: ProcessedQuery
    fused: list[RetrievalResult]
    reranked: list[RetrievalResult] | None = None


@dataclass
class RetrievalOutput:
    query: ProcessedQuery
    results: list[RetrievalResult]
    context: AssembledContext
    timings_ms: dict = field(default_factory=dict)


class RetrievalPipeline:
    def __init__(self, pool=None, client=None, *, processor=None, retriever=None, reranker=None,
                 assembler=None, token_budget: int = 4000, rerank_candidates: int = RERANK_CANDIDATES):
        self.processor = processor or QueryProcessor(client)
        self.retriever = retriever or Retriever(pool, client)
        self.reranker = reranker or LLMReranker(client)
        self.assembler = assembler or ContextAssembler(token_budget)
        self.rerank_candidates = rerank_candidates

    async def stages(self, query: str, rerank: bool = True, hyde: bool = False,
                     processed: ProcessedQuery | None = None) -> Stages:
        """Run the pipeline and expose the intermediate (fused / reranked) lists."""
        pq = processed or await self.processor.process(query)
        if hyde and pq.hyde_text is None:
            pq.hyde_text = await self.processor.generate_hyde(query)
        fused = await self.retriever.retrieve(pq, k=RETRIEVE_K, use_hyde=hyde)
        reranked = None
        if rerank and fused:
            reranked = await self.reranker.rerank(query, fused[: self.rerank_candidates])
        return Stages(pq, fused, reranked)

    async def retrieve(self, query: str, k: int = 10, rerank: bool = True, hyde: bool = False
                       ) -> list[RetrievalResult]:
        st = await self.stages(query, rerank=rerank, hyde=hyde)
        return (st.reranked if st.reranked is not None else st.fused)[:k]

    async def run(self, query: str, k: int = 8, hyde: bool = False) -> RetrievalOutput:
        with tracer.start_as_current_span("retrieval.run") as span:
            t0 = time.perf_counter()
            st = await self.stages(query, hyde=hyde)
            results = (st.reranked if st.reranked is not None else st.fused)[:k]
            context = self.assembler.assemble(results)
            timings = {"total": round((time.perf_counter() - t0) * 1000)}
            span.set_attribute("query_type", st.query.query_type)
            span.set_attribute("candidates", len(st.fused))
            span.set_attribute("chunks_used", len(context.chunks_used))
            span.set_attribute("context_tokens", context.total_tokens)
            return RetrievalOutput(st.query, results, context, timings)
