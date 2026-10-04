"""Async client for the NeuroFlow API.

ADAPT: endpoint paths live in ``_PATHS``. Request/response field names are parsed tolerantly in
``models.py``. Handles bearer auth, 429 retry with exponential backoff, SSE streaming and
polling for ingestion completion.
"""
from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, overload

import httpx

from .exceptions import (
    APIError,
    AuthenticationError,
    IngestionError,
    NeuroFlowTimeout,
    NotFoundError,
    RateLimitError,
)
from .models import Document, EvaluationResult, QueryResult

_PATHS = {
    "ingest": "/ingest",
    "ingest_status": "/ingest/{document_id}",
    "query": "/query",
    "stream": "/query/{run_id}/stream",
    "evaluations": "/evaluations",
    "pipelines": "/pipelines",
}
_DONE = {"complete", "completed", "done", "success"}
_FAILED = {"failed", "error"}
_TOKEN_KEYS = ("token", "text", "delta", "content")


class NeuroFlowClient:
    """Async client. Use as ``async with NeuroFlowClient(url, key) as client:``."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 60.0,
        max_retries: int = 5,
        backoff_base: float = 0.5,
        backoff_max: float = 30.0,
        poll_interval: float = 2.0,
        poll_timeout: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._poll_interval = poll_interval
        self._poll_timeout = poll_timeout
        self._sleep = sleep

    # ---------- lifecycle ----------
    async def __aenter__(self) -> NeuroFlowClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------- http plumbing ----------
    def _delay(self, attempt: int, response: httpx.Response) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), self._backoff_max)
            except ValueError:
                pass
        delay = min(self._backoff_base * float(2**attempt), self._backoff_max)
        return delay + random.uniform(0, delay * 0.25)

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        try:
            body = response.json()
            detail = str(body.get("detail", body)) if isinstance(body, dict) else str(body)
        except ValueError:
            detail = response.text[:300]
        code = response.status_code
        if code in (401, 403):
            raise AuthenticationError(code, detail)
        if code == 404:
            raise NotFoundError(code, detail)
        if code == 429:
            raise RateLimitError(code, detail)
        raise APIError(code, detail)

    async def _send(self, request: httpx.Request, *, stream: bool = False) -> httpx.Response:
        """Send with retry on 429 (exponential backoff, honours Retry-After)."""
        for attempt in range(self._max_retries + 1):
            response = await self._client.send(request, stream=stream)
            if response.status_code != 429 or attempt == self._max_retries:
                if stream and response.status_code >= 400:
                    await response.aread()
                    await response.aclose()
                self._raise_for_status(response)
                return response
            delay = self._delay(attempt, response)
            if stream:
                await response.aclose()
            await self._sleep(delay)
        raise RuntimeError("unreachable")  # pragma: no cover

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        request = self._client.build_request(method, path, **kwargs)
        response = await self._send(request)
        return response.json() if response.content else {}

    # ---------- ingestion ----------
    async def ingest_file(
        self,
        file_path: str | Path,
        pipeline_id: str | None = None,
        *,
        wait: bool = True,
    ) -> Document:
        """Upload and ingest a file. Waits for ingestion to complete unless ``wait=False``."""
        path = Path(file_path)
        content = path.read_bytes()  # bytes (not a stream) so retries can resend the body
        data = {"pipeline_id": pipeline_id} if pipeline_id else None
        body = await self._request(
            "POST", _PATHS["ingest"], files={"file": (path.name, content)}, data=data
        )
        doc = Document.from_dict(body)
        return await self._wait_for_ingestion(doc) if wait else doc

    async def ingest_url(
        self, url: str, pipeline_id: str | None = None, *, wait: bool = True
    ) -> Document:
        """Ingest a web page by URL. Waits for ingestion to complete unless ``wait=False``."""
        payload: dict[str, Any] = {"url": url}
        if pipeline_id:
            payload["pipeline_id"] = pipeline_id
        doc = Document.from_dict(await self._request("POST", _PATHS["ingest"], json=payload))
        return await self._wait_for_ingestion(doc) if wait else doc

    async def get_document(self, document_id: str) -> Document:
        path = _PATHS["ingest_status"].format(document_id=document_id)
        return Document.from_dict(await self._request("GET", path))

    async def _wait_for_ingestion(self, doc: Document) -> Document:
        waited = 0.0
        while True:
            if doc.status in _DONE:
                return doc
            if doc.status in _FAILED:
                raise IngestionError(f"ingestion of {doc.id} failed: {doc.raw}")
            if waited >= self._poll_timeout:
                raise NeuroFlowTimeout(f"ingestion of {doc.id} not complete after {waited:.0f}s")
            await self._sleep(self._poll_interval)
            waited += self._poll_interval
            doc = await self.get_document(doc.id)

    # ---------- query ----------
    @overload
    def query(
        self, query: str, pipeline_id: str, stream: Literal[False] = False
    ) -> Awaitable[QueryResult]: ...

    @overload
    def query(self, query: str, pipeline_id: str, stream: Literal[True]) -> AsyncIterator[str]: ...

    @overload
    def query(
        self, query: str, pipeline_id: str, stream: bool
    ) -> Awaitable[QueryResult] | AsyncIterator[str]: ...

    def query(
        self, query: str, pipeline_id: str, stream: bool = False
    ) -> Awaitable[QueryResult] | AsyncIterator[str]:
        """Run a RAG query.

        ``await client.query(q, pid)`` returns a :class:`QueryResult`.
        ``async for token in client.query(q, pid, stream=True)`` yields tokens as they arrive.
        """
        if stream:
            return self._stream_query(query, pipeline_id)
        return self._query_once(query, pipeline_id)

    async def _query_once(self, query: str, pipeline_id: str) -> QueryResult:
        body = await self._request(
            "POST", _PATHS["query"], json={"query": query, "pipeline_id": pipeline_id}
        )
        return QueryResult.from_dict(body)

    async def _stream_query(self, query: str, pipeline_id: str) -> AsyncIterator[str]:
        started = await self._request(
            "POST",
            _PATHS["query"],
            json={"query": query, "pipeline_id": pipeline_id, "stream": True},
        )
        run_id = str(started.get("run_id") or started.get("id") or "")
        if not run_id:
            raise APIError(500, f"streaming query returned no run_id: {started}")
        request = self._client.build_request(
            "GET",
            _PATHS["stream"].format(run_id=run_id),
            headers={"Accept": "text/event-stream"},
        )
        response = await self._send(request, stream=True)
        try:
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    return
                if payload:
                    yield _parse_token(payload)
        finally:
            await response.aclose()

    # ---------- evaluation ----------
    async def get_evaluation(self, run_id: str, wait: bool = True) -> EvaluationResult:
        """Get evaluation scores for a run; with ``wait=True`` polls until they are ready."""
        waited = 0.0
        while True:
            try:
                body = await self._request("GET", _PATHS["evaluations"], params={"run_id": run_id})
            except NotFoundError:
                body = {}
            if isinstance(body, dict) and "items" in body:
                body = body["items"]
            entry = body[0] if isinstance(body, list) and body else body
            if isinstance(entry, dict) and entry:
                result = EvaluationResult.from_dict({"run_id": run_id, **entry})
                if result.status in _DONE or not wait:
                    return result
                if result.status in _FAILED:
                    raise APIError(500, f"evaluation for {run_id} failed")
            elif not wait:
                raise NotFoundError(404, f"no evaluation for run {run_id} yet")
            if waited >= self._poll_timeout:
                raise NeuroFlowTimeout(f"evaluation for {run_id} not ready after {waited:.0f}s")
            await self._sleep(self._poll_interval)
            waited += self._poll_interval

    # ---------- pipelines ----------
    async def list_pipelines(self) -> list[dict[str, Any]]:
        body = await self._request("GET", _PATHS["pipelines"])
        items = body.get("items", []) if isinstance(body, dict) else body
        return list(items)

    async def create_pipeline(self, config: dict[str, Any]) -> dict[str, Any]:
        body = await self._request("POST", _PATHS["pipelines"], json=config)
        return dict(body)


def _parse_token(payload: str) -> str:
    try:
        data = json.loads(payload)
    except ValueError:
        return payload
    if isinstance(data, dict):
        for key in _TOKEN_KEYS:
            if isinstance(data.get(key), str):
                return str(data[key])
        return ""
    return str(data)
