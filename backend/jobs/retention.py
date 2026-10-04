"""Daily data-retention job (APScheduler).

Deletes:
  * pipeline_runs older than 90 days with status = 'complete' and no associated evaluations row
  * evaluations older than 180 days
  * chunks belonging to documents with status = 'archived'
and logs the deleted counts via structlog.

ADAPT: table/column names below (pipeline_runs, evaluations, documents, chunks).

Run manually (runbook, Incident 5):   python -m backend.jobs.retention
Schedule inside the API/worker:       start_scheduler(engine)   # daily at 03:00 UTC
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from sqlalchemy.ext.asyncio import AsyncEngine

Executor = Callable[[str, Mapping[str, Any]], Awaitable[int]]

RUNS_RETENTION_DAYS = 90
EVALUATIONS_RETENTION_DAYS = 180

DELETE_RUNS = """
DELETE FROM pipeline_runs
WHERE status = 'complete'
  AND created_at < :cutoff
  AND NOT EXISTS (SELECT 1 FROM evaluations e WHERE e.run_id = pipeline_runs.id)
"""
DELETE_EVALUATIONS = "DELETE FROM evaluations WHERE created_at < :cutoff"
DELETE_ARCHIVED_CHUNKS = """
DELETE FROM chunks
WHERE document_id IN (SELECT id FROM documents WHERE status = 'archived')
"""

try:
    import structlog

    _slog: Any = structlog.get_logger("retention")
except ImportError:  # pragma: no cover - structlog is a project dependency
    _slog = None
_stdlog = logging.getLogger("retention")


def _log(event: str, **fields: object) -> None:
    if _slog is not None:
        _slog.info(event, **fields)
    else:  # pragma: no cover
        _stdlog.info("%s %s", event, fields)


@dataclass(frozen=True)
class RetentionResult:
    pipeline_runs_deleted: int
    evaluations_deleted: int
    chunks_deleted: int


async def run_retention(
    execute: Executor,
    now: datetime | None = None,
    runs_days: int = RUNS_RETENTION_DAYS,
    evaluations_days: int = EVALUATIONS_RETENTION_DAYS,
) -> RetentionResult:
    """Apply the retention policy using ``execute(sql, params) -> rows_deleted``."""
    now = now or datetime.now(UTC)
    runs = await execute(DELETE_RUNS, {"cutoff": now - timedelta(days=runs_days)})
    evals = await execute(
        DELETE_EVALUATIONS, {"cutoff": now - timedelta(days=evaluations_days)}
    )
    chunks = await execute(DELETE_ARCHIVED_CHUNKS, {})
    result = RetentionResult(runs, evals, chunks)
    _log("retention_complete", **asdict(result))
    return result


def sqlalchemy_executor(engine: AsyncEngine) -> Executor:
    """Adapt a SQLAlchemy AsyncEngine to the Executor interface (one transaction per statement)."""
    from sqlalchemy import text

    async def execute(sql: str, params: Mapping[str, Any]) -> int:
        async with engine.begin() as conn:
            result = await conn.execute(text(sql), dict(params))
            return int(result.rowcount or 0)

    return execute


def make_engine(url: str | None = None) -> AsyncEngine:
    from sqlalchemy.ext.asyncio import create_async_engine

    raw = url or os.environ["POSTGRES_URL"]
    if raw.startswith("postgresql://"):
        raw = raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    return create_async_engine(raw)


def start_scheduler(engine: AsyncEngine, hour: int = 3, minute: int = 0) -> AsyncIOScheduler:
    """Start the daily job on the running event loop. Returns the scheduler."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    executor = sqlalchemy_executor(engine)

    async def job() -> None:
        try:
            await run_retention(executor)
        except Exception:
            _stdlog.exception("retention job failed")

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        job, "cron", hour=hour, minute=minute, id="data_retention", replace_existing=True
    )
    scheduler.start()
    return scheduler


async def _main() -> None:
    engine = make_engine()
    try:
        result = await run_retention(sqlalchemy_executor(engine))
        print(result)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
