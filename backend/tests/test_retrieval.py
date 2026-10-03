"""Offline tests for Task 5 (no DB, Redis, or API keys). Run from backend/:  python tests/test_retrieval.py"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("POSTGRES_PASSWORD", "x")
os.environ.setdefault("REDIS_PASSWORD", "x")
BACKEND = Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent
sys.path[:0] = [str(BACKEND), str(ROOT)]

from evaluation.retrieval_eval import hit_and_rank, summarize  # noqa: E402
from pipelines.ingestion.chunker import count_tokens  # noqa: E402
from pipelines.retrieval import (ContextAssembler, LLMReranker, ProcessedQuery, QueryProcessor,  # noqa: E402
                                 RetrievalPipeline, RetrievalResult, Retriever, reciprocal_rank_fusion)
from pipelines.retrieval.reranker import parse_score  # noqa: E402
from providers.base import GenerationResult  # noqa: E402


def R(cid, content="text", filename="doc.pdf", page=1, **kw):
    return RetrievalResult(chunk_id=cid, document_id="d-" + cid, content=content, filename=filename,
                           metadata={"page_number": page}, strategies=kw.pop("strategies", ["dense"]), **kw)


def gen(text):
    return GenerationResult(text, "fake", 1, 1, 1.0, 0.0, "stop")


class ScriptedClient:
    """chat() answers come from a function(prompt)->str; embed() returns fixed vectors."""
    def __init__(self, answer=lambda p: "5"):
        self.answer, self.prompts, self.embedded = answer, [], []

    async def chat(self, messages, routing_criteria=None, **kw):
        prompt = messages[0].content
        self.prompts.append(prompt)
        out = self.answer(prompt)
        if isinstance(out, Exception):
            raise out
        return gen(out)

    async def embed(self, texts):
        self.embedded.append(list(texts))
        return [[1.0, 0.0, float(i)] for i, _ in enumerate(texts)]


# --------------------------------------------------------------------------- fusion
def test_rrf_boosts_multi_list_chunks():
    dense = [R("a"), R("b"), R("c")]
    sparse = [R("c", strategies=["sparse"]), R("d", strategies=["sparse"]), R("a", strategies=["sparse"])]
    fused = reciprocal_rank_fusion([dense, sparse], k=60)
    scores = {r.chunk_id: r.score for r in fused}
    assert abs(scores["a"] - (1 / 61 + 1 / 63)) < 1e-12 and abs(scores["c"] - (1 / 63 + 1 / 61)) < 1e-12
    assert abs(scores["b"] - 1 / 62) < 1e-12 and abs(scores["d"] - 1 / 62) < 1e-12
    # a and c appear in both lists, so they outrank b and d which appear once
    assert {fused[0].chunk_id, fused[1].chunk_id} == {"a", "c"}
    assert {r.chunk_id for r in fused[2:]} == {"b", "d"}
    assert sorted(next(r for r in fused if r.chunk_id == "a").strategies) == ["dense", "sparse"]
    assert len(fused) == 4 and all(r.rrf_score == r.score for r in fused)


# --------------------------------------------------------------------------- query processing
async def test_query_processor_extracts_filters():
    payload = json.dumps({"expansions": ["climate change reports 2023", "global warming documents"],
                          "filters": {"year": 2023, "topic": "climate"}, "query_type": "factual"})
    client = ScriptedClient(lambda p: "```json\n" + payload + "\n```")   # code fences must be tolerated
    pq = await QueryProcessor(client).process("Show me documents from 2023 about climate change")
    assert pq.filters == {"year": 2023, "topic": "climate"} and len(pq.expansions) == 2
    assert pq.query_type == "factual"

    # LLM failure -> heuristics (year regex, query-type rules), retrieval must not break
    bad = ScriptedClient(lambda p: RuntimeError("api down"))
    pq = await QueryProcessor(bad).process("compare postgres vs mysql in 2022")
    assert pq.filters == {"year": 2022} and pq.query_type == "comparative" and pq.expansions == []
    # garbage JSON, invalid type, duplicate expansion
    junk = ScriptedClient(lambda p: json.dumps({"expansions": ["How do I set up X", "x setup guide", "x setup guide"],
                                                "filters": {"nested": {"a": 1}}, "query_type": "weird"}))
    pq = await QueryProcessor(junk).process("How do I set up X")
    assert pq.expansions == ["x setup guide"] and pq.filters == {} and pq.query_type == "procedural"


# --------------------------------------------------------------------------- retriever
class _Ctx:
    def __init__(self, v=None):
        self.v = v

    async def __aenter__(self):
        return self.v

    async def __aexit__(self, *a):
        return False


class FakePool:
    def __init__(self, delay=0.0, and_returns_nothing=False):
        self.delay, self.calls, self.and_empty = delay, [], and_returns_nothing

    def _rows(self, tag, n=2):
        return [{"id": f"{tag}{i}", "document_id": "doc", "content": f"{tag} chunk {i}", "metadata": "{}",
                 "filename": "f.pdf", "score": 1.0 - i / 10} for i in range(n)]

    async def fetch(self, sql, *args):
        await asyncio.sleep(self.delay)
        kind = ("metadata" if "metadata @>" in sql else "sparse_or" if "to_tsquery(" in sql and "plainto" not in sql
                else "sparse" if "plainto_tsquery" in sql else "dense")
        self.calls.append((kind, args))
        if kind == "sparse" and self.and_empty:
            return []
        return self._rows(kind)

    async def execute(self, *a):
        return None

    def acquire(self):
        pool = self

        class Conn:
            def transaction(self):
                return _Ctx()

            async def execute(self, sql, *a):
                pool.calls.append(("set", sql))

            async def fetch(self, sql, *a):
                return await pool.fetch(sql, *a)

        return _Ctx(Conn())


async def test_retriever_runs_three_strategies_in_parallel():
    pool = FakePool(delay=0.15)
    retr = Retriever(pool, ScriptedClient())
    pq = ProcessedQuery("what is hnsw", filters={"year": 2023})
    t0 = time.perf_counter()
    fused = await retr.retrieve(pq, k=20)
    elapsed = time.perf_counter() - t0
    kinds = [c[0] for c in pool.calls if c[0] != "set"]
    assert sorted(kinds) == ["dense", "metadata", "sparse"]
    assert elapsed < 0.30, f"strategies should overlap (3 x 0.15s sequential would be 0.45s): {elapsed:.2f}s"
    assert {r.chunk_id for r in fused} == {"dense0", "dense1", "sparse0", "sparse1", "metadata0", "metadata1"}
    assert any(c[0] == "set" and "hnsw.ef_search" in c[1] for c in pool.calls)

    # no filters -> no metadata query; expansions -> extra dense + sparse lists
    pool2 = FakePool()
    await Retriever(pool2, ScriptedClient()).retrieve(ProcessedQuery("q", expansions=["q2", "q3"]), k=5)
    kinds2 = [c[0] for c in pool2.calls if c[0] != "set"]
    assert kinds2.count("dense") == 3 and kinds2.count("sparse") == 3 and "metadata" not in kinds2


async def test_sparse_falls_back_to_or_query():
    pool = FakePool(and_returns_nothing=True)
    await Retriever(pool, ScriptedClient()).retrieve(ProcessedQuery("how does hnsw indexing work"), k=5)
    or_calls = [c for c in pool.calls if c[0] == "sparse_or"]
    assert or_calls and or_calls[0][1][0] == "how | does | hnsw | indexing | work"


async def test_hyde_embeds_hypothetical_answer():
    client = ScriptedClient()
    pq = ProcessedQuery("what is hnsw", hyde_text="HNSW is a graph index for ANN search.")
    await Retriever(FakePool(), client).retrieve(pq, k=5, use_hyde=True)
    assert client.embedded[0][0] == "HNSW is a graph index for ANN search."


# --------------------------------------------------------------------------- reranker
async def test_reranker_orders_by_llm_score():
    def answer(prompt):
        if "FAIL" in prompt:
            return RuntimeError("boom")
        return "9" if "relevant" in prompt else ("Score: 7/10" if "kinda" in prompt else "1")
    cands = [R("x", "off topic"), R("y", "kinda related"), R("z", "very relevant passage"), R("w", "FAIL item")]
    out = await LLMReranker(ScriptedClient(answer)).rerank("q", cands)
    assert [r.chunk_id for r in out] == ["z", "y", "x", "w"]          # failed scoring sinks to the end
    assert out[0].rerank_score == 9.0 and out[1].rerank_score == 7.0 and out[3].rerank_score is None
    assert len((await LLMReranker(ScriptedClient(answer)).rerank("q", cands, top_k=2))) == 2
    assert parse_score("8") == 8.0 and parse_score("no number") is None and parse_score("42") == 10.0


# --------------------------------------------------------------------------- context assembly
def test_context_assembler_respects_budget():
    sentence = "This sentence carries some meaningful retrieved content for the model to read. "
    chunks = [R(f"c{i}", sentence * 25, filename=f"doc{i}.pdf", page=i + 1) for i in range(30)]
    for budget in (500, 1200, 4000):
        out = ContextAssembler(budget).assemble(chunks)
        assert count_tokens(out.context) <= budget and out.total_tokens <= budget, (budget, out.total_tokens)
        assert out.chunks_used and len(out.chunks_used) == len(out.sources)
    out = ContextAssembler(4000).assemble(chunks[:3])
    assert out.context.startswith("[Source 1 — doc0.pdf, page 1]\n")
    assert "[Source 2 — doc1.pdf, page 2]" in out.context
    assert out.sources[0] == {"index": 1, "filename": "doc0.pdf", "page": 1, "chunk_id": "c0", "document_id": "d-c0"}
    assert set(out.to_dict()) >= {"chunks_used", "total_tokens", "sources"}

    # last chunk is cut at a SENTENCE boundary, never mid-sentence
    long_chunk = R("big", sentence * 60, filename="big.pdf")
    out = ContextAssembler(900).assemble([R("a", sentence * 25), long_chunk])
    assert out.truncated and out.chunks_used == ["a", "big"]
    assert out.context.rstrip().endswith("read.")
    # no page info -> header without a page; empty input -> empty context
    assert ContextAssembler().assemble([RetrievalResult("z", "d", "hi", filename="n.md")]).context.startswith("[Source 1 — n.md]")
    assert ContextAssembler().assemble([]).total_tokens == 0


# --------------------------------------------------------------------------- pipeline + metrics
def test_metrics():
    assert hit_and_rank(["a", "b", "c"], ["c"]) == (True, 3)
    assert hit_and_rank(["a"] * 10 + ["c"], ["c"], k=10) == (False, None)   # beyond k
    s = summarize([(True, 1), (True, 2), (False, None), (True, 4)])
    assert s == {"hit_rate": 0.75, "mrr": round((1 + 0.5 + 0 + 0.25) / 4, 4), "queries": 4}


class StubProcessor:
    async def process(self, q):
        return ProcessedQuery(q)

    async def generate_hyde(self, q):
        return "hypothetical"


class StubRetriever:
    def __init__(self, fused):
        self.fused = fused

    async def retrieve(self, pq, k=20, use_hyde=False, fuse=True):
        return list(self.fused)


async def test_pipeline_rerank_beats_rrf_baseline():
    # The right chunk sits at RRF rank 5; the reranker should lift it to rank 1.
    fused = [R(f"c{i}", "filler text about other topics.") for i in range(4)] + \
            [R("gold", "the relevant answer lives here.")] + [R("tail", "more filler.")]
    client = ScriptedClient(lambda p: "9" if "relevant" in p else "2")
    pipe = RetrievalPipeline(processor=StubProcessor(), retriever=StubRetriever(fused),
                             reranker=LLMReranker(client), assembler=ContextAssembler(4000))
    base = await pipe.retrieve("q", k=10, rerank=False)
    full = await pipe.retrieve("q", k=10, rerank=True)
    assert hit_and_rank([r.chunk_id for r in base], ["gold"])[1] == 5
    assert hit_and_rank([r.chunk_id for r in full], ["gold"])[1] == 1
    out = await pipe.run("q", k=3)
    assert out.results[0].chunk_id == "gold" and out.context.chunks_used[0] == "gold"
    assert out.context.total_tokens <= 4000
    # only the top-40 fused are reranked
    many = [R(f"m{i}", "x") for i in range(100)]
    pipe2 = RetrievalPipeline(processor=StubProcessor(), retriever=StubRetriever(many),
                              reranker=LLMReranker(ScriptedClient()), assembler=ContextAssembler())
    st = await pipe2.stages("q")
    assert len(st.fused) == 100 and len(st.reranked) == 40


async def main():
    test_rrf_boosts_multi_list_chunks(); print("PASS RRF fusion boosts multi-list chunks")
    await test_query_processor_extracts_filters(); print("PASS query processing / metadata filter extraction")
    await test_retriever_runs_three_strategies_in_parallel(); print("PASS retrieval strategies run in parallel")
    await test_sparse_falls_back_to_or_query(); print("PASS sparse OR-query fallback")
    await test_hyde_embeds_hypothetical_answer(); print("PASS HyDE embeds hypothetical answer")
    await test_reranker_orders_by_llm_score(); print("PASS reranker")
    test_context_assembler_respects_budget(); print("PASS context assembly respects token budget")
    test_metrics(); print("PASS hit-rate / MRR metrics")
    await test_pipeline_rerank_beats_rrf_baseline(); print("PASS pipeline: rerank improves rank over RRF-only")
    print("\nAll Task 5 offline tests passed")


asyncio.run(main())
