"""Storage for training pairs + finetune_jobs. InMemory (tests/demo) and Postgres (asyncpg) variants."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .extractor import DEFAULT_SYSTEM, MIN_RATING, TrainingPair


def _now():
    return datetime.now(timezone.utc)


def _j(v: Any):
    return json.loads(v) if isinstance(v, str) else (v or {})


class InMemoryFineTuneRepo:
    def __init__(self):
        self.rows: list[dict] = []        # candidate rows: run_id, query, answer, context, system_prompt,
        self.jobs: dict[str, dict] = {}   # quality_score, faithfulness, user_rating, created_at, included_in_job

    def add_row(self, **row) -> None:
        row.setdefault("system_prompt", DEFAULT_SYSTEM)
        row.setdefault("faithfulness", None)
        row.setdefault("user_rating", None)
        row.setdefault("included_in_job", None)
        row.setdefault("created_at", _now())
        self.rows.append(row)

    async def fetch_candidate_pairs(self, min_score: float, limit: int | None = None) -> list[TrainingPair]:
        out = []
        for r in sorted(self.rows, key=lambda r: r["created_at"], reverse=True):
            if (r["quality_score"] >= min_score and r["included_in_job"] is None
                    and (r["user_rating"] is None or r["user_rating"] >= MIN_RATING)):
                out.append(TrainingPair(r["run_id"], r["query"], r["context"], r["answer"], r["quality_score"],
                                        r["created_at"], r["system_prompt"], r["faithfulness"], r["user_rating"]))
        return out[:limit] if limit else out

    async def mark_included(self, run_ids: list[str], job_id: str) -> None:
        ids = set(run_ids)
        for r in self.rows:
            if r["run_id"] in ids:
                r["included_in_job"] = job_id

    async def fetch_rated_runs(self) -> list[dict]:
        return [r for r in self.rows if r["user_rating"] is not None]

    async def create_job(self, job_id: str, base_model: str, domain: str) -> None:
        now = _now()
        self.jobs[job_id] = {"id": job_id, "status": "pending", "base_model": base_model, "domain": domain,
                             "provider_job_id": None, "fine_tuned_model": None, "mlflow_run_id": None,
                             "mlflow_run_url": None, "training_pair_count": None, "dataset_path": None,
                             "metrics": {}, "error": None, "created_at": now, "updated_at": now}

    async def update_job(self, job_id: str, **fields) -> None:
        job = self.jobs[job_id]
        if "metrics" in fields:
            fields["metrics"] = {**job["metrics"], **fields["metrics"]}
        job.update(fields, updated_at=_now())

    async def get_job(self, job_id: str):
        return self.jobs.get(job_id)

    async def list_jobs(self) -> list[dict]:
        return sorted(self.jobs.values(), key=lambda j: j["created_at"], reverse=True)

    async def list_active_jobs(self) -> list[dict]:
        return [dict(j) for j in self.jobs.values() if j["status"] in ("submitted", "running")]


_RATED_SQL = """
SELECT pr.id AS run_id, pr.query, pr.generation AS answer, e.user_rating, e.overall_score AS quality_score
FROM pipeline_runs pr
JOIN LATERAL (SELECT user_rating, overall_score FROM evaluations WHERE run_id = pr.id
              ORDER BY created_at DESC LIMIT 1) e ON true
WHERE e.user_rating IS NOT NULL AND pr.generation IS NOT NULL"""


class PostgresFineTuneRepo:
    """ADAPT: assumes Task 37 tables (evaluations.user_rating / faithfulness) + migrations/task39_finetuning.sql.
    NOTE: not exercised by the offline tests."""

    def __init__(self, pool):
        self.pool = pool

    async def fetch_candidate_pairs(self, min_score: float, limit: int | None = None) -> list[TrainingPair]:
        rows = await self.pool.fetch(
            """SELECT tp.run_id, COALESCE(tp.quality_score, tp.overall_score) AS quality_score, tp.created_at,
                      pr.query, pr.generation AS answer, pr.prompt, pr.metadata, e.faithfulness, e.user_rating
               FROM training_pairs tp
               JOIN pipeline_runs pr ON pr.id = tp.run_id
               LEFT JOIN LATERAL (SELECT faithfulness, user_rating FROM evaluations WHERE run_id = pr.id
                                  ORDER BY created_at DESC LIMIT 1) e ON true
               WHERE COALESCE(tp.quality_score, tp.overall_score) >= $1
                 AND tp.included_in_job IS NULL
                 AND (e.user_rating IS NULL OR e.user_rating >= $2)
               ORDER BY tp.created_at DESC LIMIT $3""", min_score, MIN_RATING, limit or 100000)
        out = []
        for r in rows:
            chunks = _j(r["metadata"]).get("chunks") or []
            context = "\n\n".join(f"[Source {i}] {c}" for i, c in enumerate(chunks, 1))
            system = DEFAULT_SYSTEM
            try:   # Task 36 stored the exact messages sent to the LLM; reuse its system prompt
                system = json.loads(r["prompt"])[0]["content"]
            except Exception:
                pass
            out.append(TrainingPair(str(r["run_id"]), r["query"], context, r["answer"], r["quality_score"],
                                    r["created_at"], system, r["faithfulness"], r["user_rating"]))
        return out

    async def mark_included(self, run_ids: list[str], job_id: str) -> None:
        await self.pool.execute(
            "UPDATE training_pairs SET included_in_job = $2 WHERE run_id = ANY($1::uuid[])", run_ids, job_id)

    async def fetch_rated_runs(self) -> list[dict]:
        return [dict(r) for r in await self.pool.fetch(_RATED_SQL)]

    async def create_job(self, job_id: str, base_model: str, domain: str) -> None:
        await self.pool.execute("INSERT INTO finetune_jobs (id, base_model, domain) VALUES ($1, $2, $3)",
                                job_id, base_model, domain)

    async def update_job(self, job_id: str, **fields) -> None:
        sets, args = [], [job_id]
        for k, v in fields.items():
            args.append(json.dumps(v) if k == "metrics" else v)
            sets.append(f"metrics = COALESCE(metrics, '{{}}'::jsonb) || ${len(args)}::jsonb" if k == "metrics"
                        else f"{k} = ${len(args)}")
        await self.pool.execute(f"UPDATE finetune_jobs SET {', '.join(sets)}, updated_at = now() WHERE id = $1", *args)

    @staticmethod
    def _job(r) -> dict:
        d = dict(r)
        d["id"], d["metrics"] = str(d["id"]), _j(d["metrics"])
        return d

    async def get_job(self, job_id: str):
        r = await self.pool.fetchrow("SELECT * FROM finetune_jobs WHERE id = $1", job_id)
        return self._job(r) if r else None

    async def list_jobs(self) -> list[dict]:
        return [self._job(r) for r in await self.pool.fetch("SELECT * FROM finetune_jobs ORDER BY created_at DESC")]

    async def list_active_jobs(self) -> list[dict]:
        return [self._job(r) for r in await self.pool.fetch(
            "SELECT * FROM finetune_jobs WHERE status IN ('submitted', 'running')")]
