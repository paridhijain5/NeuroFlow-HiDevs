"""arq worker: pulls jobs from `queue:ingest` and runs the ingestion pipeline.

Run:  python -m worker      (from backend/)
"""
import asyncio
import logging
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncpg  # noqa: E402
from arq.connections import RedisSettings  # noqa: E402
from arq.worker import run_worker  # noqa: E402

from config import settings  # noqa: E402
from pipelines.ingestion.pipeline import IngestionPipeline  # noqa: E402
from providers.client import NeuroFlowClient  # noqa: E402
from telemetry import setup_tracing  # noqa: E402

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("worker")


async def startup(ctx):
    setup_tracing("neuroflow-worker")
    ctx["pool"] = await asyncpg.create_pool(settings.postgres_dsn, min_size=1, max_size=5)
    ctx["client"] = NeuroFlowClient(ctx["redis"])  # arq's Redis connection doubles as router/metrics store
    log.info("Worker ready; waiting for jobs on queue:ingest")


async def shutdown(ctx):
    await ctx["pool"].close()


async def process_document(ctx, document_id: str, source: str, source_type: str) -> dict:
    """`source` is a file path (pdf/docx/csv/image) or a URL (url)."""
    log.info("Processing %s (%s)", document_id, source_type)
    return await IngestionPipeline(ctx["pool"], ctx["client"]).run(document_id, source, source_type)


class WorkerSettings:
    functions = [process_document]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings(host=settings.redis_host, port=settings.redis_port,
                                   password=settings.redis_password)
    queue_name = "queue:ingest"
    max_jobs = 4
    job_timeout = 900
    max_tries = 1  # extraction failures are recorded on the document, not retried blindly


if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())  # arq needs a current loop on newer Pythons
    run_worker(WorkerSettings)
