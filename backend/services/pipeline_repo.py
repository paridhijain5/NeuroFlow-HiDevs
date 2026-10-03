"""Storage for named, versioned pipelines. Two implementations of the same interface:
  - PostgresPipelineRepo  (asyncpg pool; SQL in backend/migrations/task38_pipelines.sql)
  - InMemoryPipelineRepo  (demo + tests)
"""
from __future__ import annotations

import json, uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

from backend.models.pipeline import PipelineConfig
from .stats import avg_scores, METRICS


class DuplicateNameError(Exception): ...


@dataclass
class PipelineRecord:
    id: uuid.UUID
    name: str
    description: str
    status: str            # "active" | "archived"
    version: int
    config: dict
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict:
        d = asdict(self)
        d["id"] = str(self.id)
        return d


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _j(v: Any):
    return json.loads(v) if isinstance(v, str) else v


# --------------------------------------------------------------------------- in-memory
class InMemoryPipelineRepo:
    def __init__(self):
        self.pipelines: dict[uuid.UUID, dict] = {}
        self.versions: dict[uuid.UUID, dict[int, dict]] = {}
        self.runs: list[dict] = []
        self.evals: dict[str, dict] = {}

    # -- pipelines
    async def create(self, config: PipelineConfig) -> PipelineRecord:
        if any(p["name"] == config.name for p in self.pipelines.values()):
            raise DuplicateNameError(config.name)
        pid, now = uuid.uuid4(), _now()
        self.pipelines[pid] = {"name": config.name, "description": config.description, "status": "active",
                               "current_version": 1, "created_at": now, "updated_at": now}
        self.versions[pid] = {1: config.to_dict()}
        return await self.get(pid)

    def _record(self, pid, version=None) -> PipelineRecord | None:
        p = self.pipelines.get(pid)
        if p is None:
            return None
        v = version or p["current_version"]
        cfg = self.versions[pid].get(v)
        if cfg is None:
            return None
        return PipelineRecord(pid, p["name"], p["description"], p["status"], v, cfg, p["created_at"], p["updated_at"])

    async def get(self, pid: uuid.UUID, version: int | None = None):
        return self._record(pid, version)

    async def list_versions(self, pid):
        return [{"version": v, "config": c} for v, c in sorted(self.versions.get(pid, {}).items())]

    async def update(self, pid, config: PipelineConfig) -> PipelineRecord:
        p = self.pipelines[pid]
        new = p["current_version"] + 1
        self.versions[pid][new] = config.to_dict()          # old versions are never touched
        p.update(current_version=new, description=config.description, updated_at=_now())
        return self._record(pid)

    async def archive(self, pid) -> bool:
        p = self.pipelines.get(pid)
        if p is None:
            return False
        p.update(status="archived", updated_at=_now())
        return True

    async def list(self, include_archived: bool = False) -> list[dict]:
        out = []
        for pid, p in sorted(self.pipelines.items(), key=lambda kv: kv[1]["created_at"], reverse=True):
            if p["status"] == "archived" and not include_archived:
                continue
            runs = sorted((r for r in self.runs if r["pipeline_id"] == pid), key=lambda r: r["created_at"])
            last = runs[-1] if runs else None
            ev = self.evals.get(last["run_id"]) if last else None
            out.append({"id": str(pid), "name": p["name"], "description": p["description"],
                        "status": p["status"], "version": p["current_version"],
                        "created_at": p["created_at"],
                        "last_run": None if last is None else {
                            "run_id": last["run_id"], "at": last["created_at"],
                            "pipeline_version": last["pipeline_version"],
                            "retrieval_latency_ms": last["retrieval_latency_ms"],
                            "total_latency_ms": last["retrieval_latency_ms"] + last["latency_ms"],
                            "eval_score": ev["overall_score"] if ev else None}})
        return out

    # -- runs / evals
    def add_run(self, **row) -> None:
        row.setdefault("created_at", _now())
        row.setdefault("status", "complete")
        self.runs.append(row)

    def add_evaluation(self, run_id: str, **scores) -> None:
        self.evals[run_id] = scores

    def _rows(self, pid) -> list[dict]:
        rows = []
        for r in self.runs:
            if r["pipeline_id"] == pid and r["status"] == "complete":
                rows.append({**r, **{m: None for m in METRICS}, **self.evals.get(r["run_id"], {})})
        return rows

    async def fetch_runs_for_analytics(self, pid) -> list[dict]:
        return self._rows(pid)

    async def aggregate_scores(self, pid) -> dict:
        return avg_scores(self._rows(pid))

    async def list_runs(self, pid, limit: int, offset: int):
        rows = sorted(self._rows(pid), key=lambda r: r["created_at"], reverse=True)
        return rows[offset: offset + limit], len(rows)

    async def get_eval_score(self, run_id: str) -> float | None:
        e = self.evals.get(str(run_id))
        return e["overall_score"] if e else None


# --------------------------------------------------------------------------- postgres
_EVAL_LATERAL = """LEFT JOIN LATERAL (
        SELECT faithfulness, answer_relevance, context_precision, context_recall, overall_score
        FROM evaluations WHERE run_id = r.id ORDER BY created_at DESC LIMIT 1) e ON true"""


class PostgresPipelineRepo:
    """ADAPT: assumes pipeline_runs has pipeline_id, pipeline_version, retrieval_latency_ms,
    latency_ms, input_tokens, output_tokens, status, created_at (see migrations/task38_pipelines.sql).
    NOTE: not exercised by the offline tests."""

    def __init__(self, pool):
        self.pool = pool

    @staticmethod
    def _rec(row) -> PipelineRecord:
        return PipelineRecord(row["id"], row["name"], row["description"], row["status"], row["version"],
                              _j(row["config"]), row["created_at"], row["updated_at"])

    async def create(self, config: PipelineConfig) -> PipelineRecord:
        import asyncpg
        pid = uuid.uuid4()
        try:
            async with self.pool.acquire() as conn, conn.transaction():
                await conn.execute(
                    """INSERT INTO pipelines (id, name, description, status, current_version)
                       VALUES ($1, $2, $3, 'active', 1)""", pid, config.name, config.description)
                await conn.execute(
                    "INSERT INTO pipeline_versions (pipeline_id, version, config) VALUES ($1, 1, $2::jsonb)",
                    pid, json.dumps(config.to_dict()))
        except asyncpg.UniqueViolationError as e:
            raise DuplicateNameError(config.name) from e
        return await self.get(pid)

    async def get(self, pid, version: int | None = None):
        row = await self.pool.fetchrow(
            """SELECT p.id, p.name, p.description, p.status, p.created_at, p.updated_at, v.version, v.config
               FROM pipelines p
               JOIN pipeline_versions v ON v.pipeline_id = p.id AND v.version = COALESCE($2, p.current_version)
               WHERE p.id = $1""", pid, version)
        return self._rec(row) if row else None

    async def list_versions(self, pid):
        rows = await self.pool.fetch(
            "SELECT version, config, created_at FROM pipeline_versions WHERE pipeline_id = $1 ORDER BY version", pid)
        return [{"version": r["version"], "config": _j(r["config"]), "created_at": r["created_at"]} for r in rows]

    async def update(self, pid, config: PipelineConfig) -> PipelineRecord:
        async with self.pool.acquire() as conn, conn.transaction():
            cur = await conn.fetchval("SELECT current_version FROM pipelines WHERE id = $1 FOR UPDATE", pid)
            new = cur + 1
            await conn.execute(
                "INSERT INTO pipeline_versions (pipeline_id, version, config) VALUES ($1, $2, $3::jsonb)",
                pid, new, json.dumps(config.to_dict()))
            await conn.execute(
                "UPDATE pipelines SET current_version = $2, description = $3, updated_at = now() WHERE id = $1",
                pid, new, config.description)
        return await self.get(pid)

    async def archive(self, pid) -> bool:
        res = await self.pool.execute(
            "UPDATE pipelines SET status = 'archived', updated_at = now() WHERE id = $1", pid)
        return res.endswith("1")

    async def list(self, include_archived: bool = False) -> list[dict]:
        rows = await self.pool.fetch(
            f"""SELECT p.id, p.name, p.description, p.status, p.current_version, p.created_at,
                       r.id AS run_id, r.created_at AS run_at, r.pipeline_version,
                       r.retrieval_latency_ms, r.latency_ms, e.overall_score
                FROM pipelines p
                LEFT JOIN LATERAL (
                    SELECT * FROM pipeline_runs WHERE pipeline_id = p.id ORDER BY created_at DESC LIMIT 1) r ON true
                {_EVAL_LATERAL}
                WHERE $1 OR p.status <> 'archived'
                ORDER BY p.created_at DESC""", include_archived)
        return [{"id": str(r["id"]), "name": r["name"], "description": r["description"], "status": r["status"],
                 "version": r["current_version"], "created_at": r["created_at"],
                 "last_run": None if r["run_id"] is None else {
                     "run_id": str(r["run_id"]), "at": r["run_at"], "pipeline_version": r["pipeline_version"],
                     "retrieval_latency_ms": r["retrieval_latency_ms"],
                     "total_latency_ms": (r["retrieval_latency_ms"] or 0) + (r["latency_ms"] or 0),
                     "eval_score": r["overall_score"]}} for r in rows]

    async def fetch_runs_for_analytics(self, pid) -> list[dict]:
        rows = await self.pool.fetch(
            f"""SELECT r.id AS run_id, r.pipeline_version, r.retrieval_latency_ms, r.latency_ms,
                       r.input_tokens, r.output_tokens, r.created_at,
                       e.faithfulness, e.answer_relevance, e.context_precision, e.context_recall, e.overall_score
                FROM pipeline_runs r {_EVAL_LATERAL}
                WHERE r.pipeline_id = $1 AND r.status = 'complete'""", pid)
        return [dict(r) for r in rows]

    async def aggregate_scores(self, pid) -> dict:
        return avg_scores(await self.fetch_runs_for_analytics(pid))

    async def list_runs(self, pid, limit: int, offset: int):
        rows = await self.fetch_runs_for_analytics(pid)
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return rows[offset: offset + limit], len(rows)

    async def get_eval_score(self, run_id) -> float | None:
        return await self.pool.fetchval(
            "SELECT overall_score FROM evaluations WHERE run_id = $1 ORDER BY created_at DESC LIMIT 1", run_id)
