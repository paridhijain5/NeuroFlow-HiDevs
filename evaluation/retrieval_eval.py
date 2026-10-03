"""Retrieval evaluation: Hit Rate@10 and MRR@10 for the baseline and the full pipeline.

Run from backend/ (so ../.env is found):
    python ../evaluation/retrieval_eval.py --generate 20     # build evaluation/test_set.json from your chunks
    python ../evaluation/retrieval_eval.py --run             # score the pipeline, write retrieval_results.json
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "backend"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

TEST_SET = ROOT / "evaluation" / "test_set.json"
RESULTS = ROOT / "evaluation" / "retrieval_results.json"
K = 10
HIT_RATE_THRESHOLD, MRR_THRESHOLD = 0.75, 0.55


# ----------------------------------------------------------------------------- metrics
def hit_and_rank(retrieved_ids: list[str], relevant_ids: list[str], k: int = K):
    """Return (hit, rank) where rank is the 1-based position of the first relevant chunk, else None."""
    relevant = set(relevant_ids)
    rank = next((i + 1 for i, cid in enumerate(retrieved_ids[:k]) if cid in relevant), None)
    return rank is not None, rank


def summarize(per_query: list[tuple[bool, int | None]]) -> dict:
    n = len(per_query) or 1
    return {"hit_rate": round(sum(h for h, _ in per_query) / n, 4),
            "mrr": round(sum((1 / r) if r else 0.0 for _, r in per_query) / n, 4),
            "queries": len(per_query)}


# ----------------------------------------------------------------------------- setup
async def build_pipeline():
    import asyncpg
    import redis.asyncio as aioredis

    from config import settings
    from pipelines.retrieval import RetrievalPipeline
    from providers.client import NeuroFlowClient

    pool = await asyncpg.create_pool(settings.postgres_dsn, min_size=1, max_size=5)
    redis = aioredis.Redis(host=settings.redis_host, port=settings.redis_port, password=settings.redis_password)
    client = NeuroFlowClient(redis)
    return pool, redis, client, RetrievalPipeline(pool, client)


QUESTION_PROMPT = ("Write ONE question a user might ask that is answered by the passage below. "
                   "Do not copy more than three consecutive words from the passage. "
                   "Output only the question.\n\nPassage:\n{passage}")


async def generate_test_set(n: int) -> None:
    from providers.base import ChatMessage
    from providers.router import RoutingCriteria

    pool, redis, client, _ = await build_pipeline()
    try:
        total = await pool.fetchval("SELECT count(*) FROM chunks")
        print(f"{total} chunks in the database")
        if total < 50:
            print("WARNING: the task asks for at least 50 chunks before evaluating; ingest more documents.")
        rows = await pool.fetch(
            "SELECT c.id, c.document_id, c.content FROM chunks c WHERE length(c.content) > 200 "
            "AND COALESCE(c.metadata->>'chunk_role','') <> 'parent' ORDER BY random() LIMIT $1", n * 4)
        by_doc: dict[str, list] = {}
        for r in rows:
            by_doc.setdefault(str(r["document_id"]), []).append(r)
        picked = []
        while len(picked) < n and any(by_doc.values()):   # round-robin across documents
            for docs in by_doc.values():
                if docs and len(picked) < n:
                    picked.append(docs.pop())
        random.shuffle(picked)

        async def make(r):
            res = await client.chat([ChatMessage("user", QUESTION_PROMPT.format(passage=r["content"][:3000]))],
                                    RoutingCriteria(task_type="classification"), temperature=0.4, max_tokens=60)
            return {"query": res.content.strip().strip('"'), "relevant_chunk_ids": [str(r["id"])]}

        test_set = await asyncio.gather(*(make(r) for r in picked))
        TEST_SET.write_text(json.dumps(test_set, indent=2))
        print(f"Wrote {len(test_set)} questions to {TEST_SET}")
    finally:
        await pool.close()
        await redis.aclose()


async def run_eval(use_hyde: bool) -> dict:
    pool, redis, client, pipeline = await build_pipeline()
    try:
        tests = json.loads(TEST_SET.read_text())
        variants: dict[str, list] = {k: [] for k in ("dense_only", "rrf_only", "rrf_plus_rerank")}
        if use_hyde:
            variants.update({"dense_only_hyde": [], "rrf_plus_rerank_hyde": []})
        details = []
        for i, t in enumerate(tests, 1):
            q, rel = t["query"], t["relevant_chunk_ids"]
            pq = await pipeline.processor.process(q)
            plain = await pipeline.stages(q, rerank=True, processed=dataclasses.replace(pq))
            dense = await pipeline.retriever.dense_only(q, K)
            row = {"query": q, "query_type": pq.query_type, "filters": pq.filters, "expansions": pq.expansions}
            for name, res in (("dense_only", dense), ("rrf_only", plain.fused),
                              ("rrf_plus_rerank", plain.reranked or plain.fused)):
                hit, rank = hit_and_rank([r.chunk_id for r in res], rel)
                variants[name].append((hit, rank)); row[name] = rank
            if use_hyde:
                hyde_pq = dataclasses.replace(pq)
                hyde_pq.hyde_text = await pipeline.processor.generate_hyde(q)
                hs = await pipeline.stages(q, rerank=True, hyde=True, processed=hyde_pq)
                hv = await client.embed([hyde_pq.hyde_text or q])
                hdense = (await pipeline.retriever._dense_retrieval(hv, K))[0]
                for name, res in (("dense_only_hyde", hdense), ("rrf_plus_rerank_hyde", hs.reranked or hs.fused)):
                    hit, rank = hit_and_rank([r.chunk_id for r in res], rel)
                    variants[name].append((hit, rank)); row[name] = rank
            details.append(row)
            print(f"[{i}/{len(tests)}] " + "  ".join(f"{k}={row[k]}" for k in variants))

        summary = {name: summarize(v) for name, v in variants.items()}
        primary = summary["rrf_plus_rerank"]
        out = {
            "k": K,
            "num_queries": len(tests),
            "variants": summary,
            "primary": "rrf_plus_rerank",
            "thresholds": {"hit_rate": HIT_RATE_THRESHOLD, "mrr": MRR_THRESHOLD},
            "passed": primary["hit_rate"] > HIT_RATE_THRESHOLD and primary["mrr"] > MRR_THRESHOLD,
            "rerank_improves_mrr": primary["mrr"] > summary["rrf_only"]["mrr"],
            "rrf_beats_dense_only": summary["rrf_only"]["hit_rate"] >= summary["dense_only"]["hit_rate"],
            "details": details,
        }
        if use_hyde:
            out["hyde_hit_rate_delta"] = round(
                summary["rrf_plus_rerank_hyde"]["hit_rate"] - primary["hit_rate"], 4)
            out["hyde_dense_hit_rate_delta"] = round(
                summary["dense_only_hyde"]["hit_rate"] - summary["dense_only"]["hit_rate"], 4)
        RESULTS.write_text(json.dumps(out, indent=2))
        print("\n" + json.dumps({k: v for k, v in out.items() if k != "details"}, indent=2))
        print(f"\nPASSED thresholds: {out['passed']}   (results saved to {RESULTS})")
        return out
    finally:
        await pool.close()
        await redis.aclose()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate", type=int, metavar="N", help="generate N test questions from stored chunks")
    ap.add_argument("--run", action="store_true", help="evaluate the pipeline")
    ap.add_argument("--no-hyde", action="store_true", help="skip the HyDE comparison (cheaper)")
    args = ap.parse_args()
    if args.generate:
        asyncio.run(generate_test_set(args.generate))
    elif args.run:
        asyncio.run(run_eval(use_hyde=not args.no_hyde))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
