"""Web page extraction: async fetch, robots.txt, SSRF guard, trafilatura."""
from __future__ import annotations

import asyncio
import ipaddress
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura

from ..models import ExtractionError, ExtractedPage

USER_AGENT = "NeuroFlowBot/1.0"
MAX_REDIRECTS = 5


async def assert_public_host(host: str | None) -> None:
    """Refuse localhost / private / link-local targets (SSRF protection)."""
    if not host:
        raise ExtractionError("URL has no host")
    try:
        ips = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, None)
        except OSError as exc:
            raise ExtractionError(f"Cannot resolve host {host}: {exc}") from exc
        ips = [ipaddress.ip_address(i[4][0]) for i in infos]
    for ip in ips:
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise ExtractionError(f"Refusing to fetch non-public address {ip}")


def _check_scheme(url: str):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ExtractionError("Only http(s) URLs are supported")
    return parsed


async def _allowed_by_robots(client: httpx.AsyncClient, url: str) -> bool:
    parsed = urlparse(url)
    try:
        resp = await client.get(f"{parsed.scheme}://{parsed.netloc}/robots.txt")
    except httpx.HTTPError:
        return True  # robots.txt unreachable: proceed
    if resp.status_code in (401, 403) or resp.status_code >= 500:
        return False
    if resp.status_code != 200:
        return True  # other 4xx: no robots.txt exists
    parser = RobotFileParser()
    parser.parse(resp.text.splitlines())
    return parser.can_fetch(USER_AGENT, url)


async def _fetch(client: httpx.AsyncClient, url: str, validate_host: bool) -> str:
    """GET with manual redirects so every hop is re-validated."""
    for _ in range(MAX_REDIRECTS + 1):
        parsed = _check_scheme(url)
        if validate_host:
            await assert_public_host(parsed.hostname)
        resp = await client.get(url)
        if resp.is_redirect and resp.headers.get("location"):
            url = urljoin(url, resp.headers["location"])
            continue
        resp.raise_for_status()
        return resp.text
    raise ExtractionError("Too many redirects")


async def extract_url(url: str, *, http_client: httpx.AsyncClient | None = None,
                      validate_host: bool = True) -> list[ExtractedPage]:
    parsed = _check_scheme(url)
    if validate_host:
        await assert_public_host(parsed.hostname)
    own = http_client is None
    client = http_client or httpx.AsyncClient(timeout=20, follow_redirects=False,
                                              headers={"User-Agent": USER_AGENT})
    try:
        if not await _allowed_by_robots(client, url):
            raise ExtractionError(f"robots.txt disallows fetching {url}")
        try:
            html = await _fetch(client, url, validate_host)
        except httpx.HTTPError as exc:
            raise ExtractionError(f"Fetch failed: {exc}") from exc
    finally:
        if own:
            await client.aclose()

    text = await asyncio.to_thread(trafilatura.extract, html, include_tables=True,
                                   include_comments=False, url=url)
    if not text:
        raise ExtractionError("No main content could be extracted from the page")
    meta = await asyncio.to_thread(trafilatura.extract_metadata, html, default_url=url)
    return [ExtractedPage(1, text, "text", {
        "extractor": "url",
        "source_url": url,
        "title": getattr(meta, "title", None),
        "author": getattr(meta, "author", None),
        "canonical_url": getattr(meta, "url", None) or url,
        "publish_date": getattr(meta, "date", None),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    })]
