import logging
from pathlib import Path

import asyncpg

log = logging.getLogger(__name__)


async def apply_migrations(pool: asyncpg.Pool, migrations_dir: str) -> None:
    """Apply infra/init/*.sql if the schema is missing (idempotent)."""
    async with pool.acquire() as conn:
        applied = await conn.fetchval("SELECT to_regclass('public.documents') IS NOT NULL")
        if applied:
            log.info("Schema already applied; skipping migrations")
            return
        files = sorted(Path(migrations_dir).glob("*.sql"))
        if not files:
            log.warning("No migration files found in %s", migrations_dir)
            return
        for f in files:
            log.info("Applying %s", f.name)
            await conn.execute(f.read_text())
