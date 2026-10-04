import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from neuroflow import (
    AuthenticationError,
    IngestionError,
    NeuroFlowClient,
    NeuroFlowTimeout,
    RateLimitError,
)


async def _no_sleep(_: float) -> None:
    return None


def make_client(handler: Any, **kw: Any) -> NeuroFlowClient:
    return NeuroFlowClient(
        "https://api.test",
        "secret-key",
        transport=httpx.MockTransport(handler),
        sleep=_no_sleep,
        **kw,
    )


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_sends_bearer_header() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(200, json=[])

    async def go() -> None:
        async with make_client(handler) as c:
            await c.list_pipelines()

    run(go())
    assert seen["auth"] == "Bearer secret-key"


def test_retries_on_429_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "1"}, json={"detail": "slow down"})
        return httpx.Response(200, json={"run_id": "r1", "answer": "hi", "citations": []})

    async def go() -> Any:
        async with make_client(handler) as c:
            return await c.query("q", "p")

    result = run(go())
    assert calls["n"] == 3 and result.answer == "hi" and result.run_id == "r1"


def test_429_exhausted_raises_rate_limit_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"detail": "nope"})

    async def go() -> None:
        async with make_client(handler, max_retries=2) as c:
            await c.query("q", "p")

    with pytest.raises(RateLimitError):
        run(go())


def test_401_raises_authentication_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "bad key"})

    async def go() -> None:
        async with make_client(handler) as c:
            await c.list_pipelines()

    with pytest.raises(AuthenticationError):
        run(go())


def test_backoff_delays_grow_exponentially() -> None:
    delays: list[float] = []

    async def record(d: float) -> None:
        delays.append(d)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={})

    async def go() -> None:
        c = NeuroFlowClient(
            "https://api.test", "k", transport=httpx.MockTransport(handler), sleep=record,
            max_retries=3, backoff_base=1.0,
        )
        try:
            await c.list_pipelines()
        finally:
            await c.aclose()

    with pytest.raises(RateLimitError):
        run(go())
    assert len(delays) == 3
    assert delays[0] < delays[1] < delays[2]


def test_streaming_yields_tokens_progressively() -> None:
    sse = 'data: {"token": "Hel"}\n\ndata: {"token": "lo"}\n\ndata: plain\n\ndata: [DONE]\n\n'

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert json.loads(request.content)["stream"] is True
            return httpx.Response(200, json={"run_id": "r9"})
        assert request.url.path == "/query/r9/stream"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse)

    async def go() -> list[str]:
        async with make_client(handler) as c:
            return [t async for t in c.query("q", "p", stream=True)]

    assert run(go()) == ["Hel", "lo", "plain"]


def test_ingest_file_polls_until_complete(tmp_path: Path) -> None:
    f = tmp_path / "doc.txt"
    f.write_text("hello")
    statuses = iter(["processing", "processing", "complete"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert b"hello" in request.content
            return httpx.Response(202, json={"id": "d1", "status": "queued"})
        return httpx.Response(200, json={"id": "d1", "status": next(statuses)})

    async def go() -> Any:
        async with make_client(handler) as c:
            return await c.ingest_file(f, "p")

    doc = run(go())
    assert doc.id == "d1" and doc.status == "complete"


def test_ingestion_failure_raises(tmp_path: Path) -> None:
    f = tmp_path / "doc.txt"
    f.write_text("x")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "d1", "status": "failed"})

    async def go() -> None:
        async with make_client(handler) as c:
            await c.ingest_file(f)

    with pytest.raises(IngestionError):
        run(go())


def test_ingestion_poll_timeout(tmp_path: Path) -> None:
    f = tmp_path / "doc.txt"
    f.write_text("x")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "d1", "status": "processing"})

    async def go() -> None:
        async with make_client(handler, poll_interval=1.0, poll_timeout=3.0) as c:
            await c.ingest_file(f)

    with pytest.raises(NeuroFlowTimeout):
        run(go())


def test_get_evaluation_waits_until_ready() -> None:
    responses = iter([[], [{"status": "running"}], [{"status": "complete", "scores": {"overall": 0.8}}]])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(responses))

    async def go() -> Any:
        async with make_client(handler) as c:
            return await c.get_evaluation("r1")

    ev = run(go())
    assert ev.status == "complete" and ev.overall == pytest.approx(0.8)


def test_pipelines_roundtrip() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json={"id": "p2", **json.loads(request.content)})
        return httpx.Response(200, json={"items": [{"id": "p1"}]})

    async def go() -> tuple[Any, Any]:
        async with make_client(handler) as c:
            return await c.list_pipelines(), await c.create_pipeline({"name": "x"})

    listed, created = run(go())
    assert listed == [{"id": "p1"}] and created["id"] == "p2"
