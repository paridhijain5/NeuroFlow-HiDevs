"""EvaluationJudge: runs the four metrics in parallel, persists results, extracts training pairs."""
from __future__ import annotations

import asyncio, json, logging, time, uuid
from dataclasses import dataclass, asdict

from opentelemetry import trace

from backend.monitoring import metrics, tracing
from backend.monitoring.tracing import SPANS

from .llm import LLM, Embedder
from .metrics import (evaluate_faithfulness, evaluate_answer_relevance,
                      evaluate_context_precision, evaluate_context_recall)

log = logging.getLogger("neuroflow.evaluation")
tracer = trace.get_tracer("neuroflow.evaluation")

WEIGHTS = {"faithfulness": 0.35, "answer_relevance": 0.30, "context_precision": 0.20, "context_recall": 0.15}
TRAINING_THRESHOLD = 0.8


@dataclass
class EvaluationResult:
    run_id: str
    faithfulness: float
    answer_relevance: float
    context_precision: float
    context_recall: float
    overall_score: float
    latency_ms: int
    training_candidate: bool

    def to_dict(self) -> dict:
        return asdict(self)


def overall(f: float, ar: float, cp: float, cr: float) -> float:
    return (WEIGHTS["faithfulness"] * f + WEIGHTS["answer_relevance"] * ar
            + WEIGHTS["context_precision"] * cp + WEIGHTS["context_recall"] * cr)


class EvaluationJudge:
    """
    `llm` MUST be a capable model routed with task_type="evaluation" (see evaluation.llm.RouterLLM);
    never a fine-tuned model.
    ADAPT: `db` = asyncpg pool (execute / fetchrow).
    """

    def __init__(self, db, llm: LLM, embedder: Embedder, training_threshold: float = TRAINING_THRESHOLD):
        self.db, self.llm, self.embedder, self.threshold = db, llm, embedder, training_threshold

    @staticmethod
    async def _traced(name: str, coro, run_id, pipeline_id):
        with tracing.span(name, run_id=run_id, pipeline_id=pipeline_id) as sp:
            value = await coro
            sp.set_attribute("score", value)
            return value

    async def evaluate(self, run_id: str, query: str, answer: str, chunks: list[str],
                       pipeline_id: str | None = None) -> EvaluationResult:
        context = "\n\n".join(f"[Source {i}] {c}" for i, c in enumerate(chunks, 1))
        start = time.perf_counter()
        with tracing.span(SPANS.JUDGE, run_id=run_id, pipeline_id=pipeline_id, chunk_count=len(chunks)) as span:
            # 1. all four metrics in parallel
            t = self._traced
            f, ar, cp, cr = await asyncio.gather(
                t(SPANS.FAITHFULNESS, evaluate_faithfulness(query, answer, context, llm=self.llm), run_id, pipeline_id),
                t(SPANS.ANSWER_RELEVANCE, evaluate_answer_relevance(query, answer, llm=self.llm,
                                                                    embedder=self.embedder), run_id, pipeline_id),
                t(SPANS.CONTEXT_PRECISION, evaluate_context_precision(query, chunks, answer, llm=self.llm),
                  run_id, pipeline_id),
                t(SPANS.CONTEXT_RECALL, evaluate_context_recall(query, chunks, answer, llm=self.llm),
                  run_id, pipeline_id),
            )
            # 2. weighted overall
            score = overall(f, ar, cp, cr)
            latency_ms = int((time.perf_counter() - start) * 1000)
            log.info("evaluated run=%s in %dms (parallel) f=%.2f ar=%.2f cp=%.2f cr=%.2f overall=%.2f",
                     run_id, latency_ms, f, ar, cp, cr, score)

            # 6. OpenTelemetry span with all four metrics
            span.set_attribute("evaluation.run_id", str(run_id))
            span.set_attribute("evaluation.faithfulness", f)
            span.set_attribute("evaluation.answer_relevance", ar)
            span.set_attribute("evaluation.context_precision", cp)
            span.set_attribute("evaluation.context_recall", cr)
            span.set_attribute("evaluation.overall_score", score)

            span.set_attribute("latency_ms", latency_ms)
            metrics.record_evaluation(pipeline_id, faithfulness=f, answer_relevance=ar, context_precision=cp,
                                      context_recall=cr, overall=score)
            candidate = score > self.threshold
            await self._persist(run_id, f, ar, cp, cr, score, latency_ms, candidate)

        return EvaluationResult(str(run_id), f, ar, cp, cr, score, latency_ms, candidate)

    async def _persist(self, run_id, f, ar, cp, cr, score, latency_ms, candidate) -> None:
        # 3. write to evaluations
        await self.db.execute(
            """INSERT INTO evaluations
               (id, run_id, faithfulness, answer_relevance, context_precision, context_recall,
                overall_score, metadata)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)""",
            str(uuid.uuid4()), run_id, f, ar, cp, cr, score,
            json.dumps({"judge_latency_ms": latency_ms}),
        )
        # 4. high-quality run -> candidate training pair
        if candidate:
            await self.db.execute(
                """UPDATE pipeline_runs
                   SET metadata = COALESCE(metadata, '{}'::jsonb) || '{"training_candidate": true}'::jsonb
                   WHERE id = $1""", run_id)
            await self.db.execute(
                """INSERT INTO training_pairs (run_id, prompt, completion, overall_score)
                   SELECT id, prompt, generation, $2 FROM pipeline_runs WHERE id = $1""",
                run_id, score)

    async def evaluate_run(self, run_id: str) -> EvaluationResult:
        """Load a finished pipeline_runs row (written by Task 36) and evaluate it."""
        row = await self.db.fetchrow(
            "SELECT query, generation, metadata, pipeline_id FROM pipeline_runs WHERE id = $1", run_id)
        if row is None or not row["generation"]:
            raise ValueError(f"run {run_id} not found or has no generation")
        meta = row["metadata"]
        meta = json.loads(meta) if isinstance(meta, str) else (meta or {})
        chunks = meta.get("chunks")
        if not chunks:
            raise ValueError("pipeline_runs.metadata.chunks missing - use the updated Task 36 generator.py")
        pid = row.get("pipeline_id")
        return await self.evaluate(run_id, row["query"], row["generation"], chunks,
                                   pipeline_id=str(pid) if pid else None)
