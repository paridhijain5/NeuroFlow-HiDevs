"""install_observability(app): tracing + GET /metrics + gauge refresher."""
from __future__ import annotations

import asyncio, logging

from fastapi import FastAPI, Response

from . import metrics, tracing

log = logging.getLogger("neuroflow.monitoring")


def install_observability(app: FastAPI, *, redis=None, service_name: str = "neuroflow-api",
                          otlp_endpoint: str | None = None, exporter=None, simple_spans: bool = False,
                          refresh_seconds: float = 5.0) -> None:
    tracing.setup_tracing(service_name, otlp_endpoint, exporter=exporter, simple=simple_spans)

    @app.get("/metrics", include_in_schema=False)
    async def _metrics():
        body, content_type = metrics.render_metrics()
        return Response(body, media_type=content_type)

    if redis is not None:
        async def _loop():
            while True:
                try:
                    await metrics.refresh_from_redis(redis)
                except Exception:
                    log.warning("gauge refresh failed", exc_info=True)
                await asyncio.sleep(refresh_seconds)

        @app.on_event("startup")
        async def _start():
            app.state.metrics_refresher = asyncio.create_task(_loop())

        @app.on_event("shutdown")
        async def _stop():
            app.state.metrics_refresher.cancel()
