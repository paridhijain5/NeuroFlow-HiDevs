"""Async helpers used by the integration tests (names match the task snippets)."""
import asyncio
import time
from pathlib import Path

import httpx

from tests import config

_tokens: dict = {}


async def get_token(client_id: str | None = None, secret: str | None = None) -> str:
    cid = client_id or config.CLIENT_ID
    cached = _tokens.get(cid)
    if cached and cached[1] > time.time() + 30:
        return cached[0]
    async with httpx.AsyncClient(base_url=config.BASE_URL, timeout=30) as c:
        r = await c.post(
            config.AUTH_TOKEN,
            json={"client_id": cid, "client_secret": secret or config.CLIENT_SECRET},
        )
        r.raise_for_status()
        body = r.json()
    _tokens[cid] = (body["access_token"], time.time() + body.get("expires_in", 3600))
    return body["access_token"]


async def api(method: str, path: str, *, auth: bool = True, timeout: float = 30, **kw) -> httpx.Response:
    headers = kw.pop("headers", {})
    if auth:
        headers["Authorization"] = f"Bearer {await get_token()}"
    async with httpx.AsyncClient(base_url=config.BASE_URL, timeout=timeout) as c:
        return await c.request(method, path, headers=headers, **kw)


async def poll(fetch, done, timeout: float, interval: float = 1.0, what: str = "condition"):
    """Call fetch() until done(result) is truthy; raise TimeoutError otherwise."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = await fetch()
        if done(last):
            return last
        await asyncio.sleep(interval)
    raise TimeoutError(f"Timed out after {timeout}s waiting for {what}; last={last!r}")


# ---------- ingestion ----------
async def ingest_file(path: str | Path) -> httpx.Response:
    p = Path(path)
    r = await api(
        "POST", config.INGEST,
        files={"file": (p.name, p.read_bytes(), "application/pdf")},
        timeout=120,
    )
    assert r.status_code in (200, 201, 202), f"ingest failed: {r.status_code} {r.text}"
    return r


async def ingest_test_document(path: str | Path) -> str:
    return (await ingest_file(path)).json()["document_id"]


async def wait_for_status(doc_id: str, status: str, timeout: float = 60) -> dict:
    async def fetch():
        r = await api("GET", config.DOC_STATUS.format(doc_id=doc_id))
        r.raise_for_status()
        return r.json()

    def done(doc):
        if doc.get("status") == "failed":
            raise AssertionError(f"document {doc_id} failed ingestion: {doc}")
        return doc.get("status") == status

    return await poll(fetch, done, timeout, what=f"document {doc_id} -> {status}")


# ---------- query / generation / evaluation ----------
async def submit_query(query: str) -> str:
    r = await api("POST", config.QUERY, json={"query": query})
    assert r.status_code in (200, 201, 202), f"query failed: {r.status_code} {r.text}"
    return r.json()["run_id"]


async def wait_for_generation(run_id: str, timeout: float = 30) -> dict:
    async def fetch():
        r = await api("GET", config.RUN.format(run_id=run_id))
        r.raise_for_status()
        return r.json()

    return await poll(fetch, lambda x: bool(x.get("generation")), timeout, what=f"generation {run_id}")


async def wait_for_evaluation(run_id: str, timeout: float = 120) -> dict:
    async def fetch():
        r = await api("GET", config.RUN_EVAL.format(run_id=run_id))
        return r.json() if r.status_code == 200 else {}

    return await poll(fetch, lambda x: "overall_score" in x, timeout, what=f"evaluation {run_id}")


async def server_is_up() -> bool:
    try:
        async with httpx.AsyncClient(base_url=config.BASE_URL, timeout=5) as c:
            return (await c.get(config.HEALTH)).status_code < 500
    except httpx.HTTPError:
        return False
