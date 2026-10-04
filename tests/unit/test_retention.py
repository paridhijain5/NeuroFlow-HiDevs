"""Retention job test: insert old + fresh fake records into in-memory SQLite and verify deletions."""
import asyncio
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.jobs.retention import run_retention

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def iso(days_ago: int) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def build_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.executescript(
        """
        CREATE TABLE pipeline_runs (id TEXT PRIMARY KEY, status TEXT, created_at TEXT);
        CREATE TABLE evaluations (id TEXT PRIMARY KEY, run_id TEXT, created_at TEXT);
        CREATE TABLE documents (id TEXT PRIMARY KEY, status TEXT);
        CREATE TABLE chunks (id TEXT PRIMARY KEY, document_id TEXT);
        """
    )
    runs = [
        ("old_complete_no_eval", "complete", iso(120)),   # deleted
        ("old_complete_with_eval", "complete", iso(120)), # kept (has evaluation)
        ("old_failed", "failed", iso(120)),               # kept (not complete)
        ("fresh_complete", "complete", iso(10)),          # kept (too recent)
    ]
    db.executemany("INSERT INTO pipeline_runs VALUES (?,?,?)", runs)
    db.executemany(
        "INSERT INTO evaluations VALUES (?,?,?)",
        [
            ("e_old", "old_complete_with_eval", iso(200)),  # deleted (>180d)
            ("e_new", "fresh_complete", iso(5)),            # kept
        ],
    )
    db.executemany(
        "INSERT INTO documents VALUES (?,?)", [("d_arch", "archived"), ("d_live", "complete")]
    )
    db.executemany(
        "INSERT INTO chunks VALUES (?,?)",
        [("c1", "d_arch"), ("c2", "d_arch"), ("c3", "d_live")],
    )
    db.commit()
    return db


def make_executor(db: sqlite3.Connection) -> Any:
    async def execute(sql: str, params: Mapping[str, Any]) -> int:
        bound = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in params.items()}
        cur = db.execute(sql, bound)
        db.commit()
        return cur.rowcount

    return execute


def ids(db: sqlite3.Connection, table: str) -> set[str]:
    return {r[0] for r in db.execute(f"SELECT id FROM {table}")}


def test_deletes_expected_records_and_reports_counts() -> None:
    db = build_db()
    result = asyncio.run(run_retention(make_executor(db), now=NOW))
    assert (result.pipeline_runs_deleted, result.evaluations_deleted, result.chunks_deleted) == (1, 1, 2)
    assert ids(db, "pipeline_runs") == {"old_complete_with_eval", "old_failed", "fresh_complete"}
    assert ids(db, "evaluations") == {"e_new"}
    assert ids(db, "chunks") == {"c3"}


def test_idempotent_second_run_deletes_nothing_new_except_newly_eligible() -> None:
    db = build_db()
    asyncio.run(run_retention(make_executor(db), now=NOW))
    second = asyncio.run(run_retention(make_executor(db), now=NOW))
    # old_complete_with_eval lost its evaluation on run 1, so it becomes eligible on run 2
    assert second.pipeline_runs_deleted == 1
    assert second.evaluations_deleted == 0 and second.chunks_deleted == 0
    assert ids(db, "pipeline_runs") == {"old_failed", "fresh_complete"}


def test_nothing_old_means_nothing_deleted() -> None:
    db = build_db()
    result = asyncio.run(run_retention(make_executor(db), now=NOW - timedelta(days=400)))
    assert result.pipeline_runs_deleted == 0 and result.evaluations_deleted == 0
