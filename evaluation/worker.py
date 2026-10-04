"""Consumes the `eval_jobs` Redis list that Task 36's generator pushes to.
Run:  python -m evaluation.worker
"""
import asyncio, json, logging, os

import asyncpg
from opentelemetry import context as otel_context
import redis.asyncio as aioredis

from backend.monitoring import tracing

from .bootstrap import build_llm_and_embedder
from .judge import EvaluationJudge

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("neuroflow.evaluation.worker")


async def main() -> None:
    db = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    redis = aioredis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    llm, embedder = build_llm_and_embedder()
    judge = EvaluationJudge(db, llm, embedder)
    log.info("evaluation worker waiting for jobs...")
    while True:
        item = await redis.brpop("eval_jobs", timeout=5)
        if item is None:
            continue
        payload = json.loads(item[1])
        run_id = payload["run_id"]
        token = otel_context.attach(tracing.extract_trace_context(payload.get("trace")))   # join the API's trace
        try:
            res = await judge.evaluate_run(run_id)
            log.info("run %s overall=%.3f", run_id, res.overall_score)
        except Exception:
            log.exception("evaluation failed for run %s", run_id)
        finally:
            otel_context.detach(token)


if __name__ == "__main__":
    asyncio.run(main())
