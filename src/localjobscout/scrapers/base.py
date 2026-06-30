from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import ClassVar
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from localjobscout.db import Job
from localjobscout.scrapers import fetcher

logger = logging.getLogger(__name__)

USER_AGENT = "LocalJobScout/0.1 (personal use)"

_robots_cache: dict[str, RobotFileParser] = {}


async def _get_robots(client: httpx.AsyncClient, host: str) -> RobotFileParser:
    if host in _robots_cache:
        return _robots_cache[host]

    parser = RobotFileParser()
    robots_url = f"https://{host}/robots.txt"
    try:
        resp = await client.get(robots_url, follow_redirects=True, timeout=10.0)
        if resp.status_code < 400:
            parser.parse(resp.text.splitlines())
        else:
            # Treat inaccessible robots.txt as allowing everything.
            parser.allow_all = True  # type: ignore[attr-defined]
    except httpx.HTTPError:
        parser.allow_all = True  # type: ignore[attr-defined]

    _robots_cache[host] = parser
    return parser


async def polite_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    delay_seconds: float = 2.0,
    source: str | None = None,
) -> httpx.Response | None:
    """Fetch a URL politely (robots + delay), preferring the Scrapling adapter.

    When the fetch adapter is active it handles the GET (anti-bot resilient)
    and the result is wrapped in an ``httpx.Response`` so callers are
    unchanged. If the adapter is inactive or fails, this falls back to the
    original httpx GET — identical to pre-integration behaviour.
    """
    parsed = urlparse(url)
    host = parsed.netloc

    robots = await _get_robots(client, host)
    if not robots.can_fetch(USER_AGENT, url):
        logger.warning("robots.txt disallows %s for %s", url, USER_AGENT)
        return None

    await asyncio.sleep(delay_seconds)

    # ── Preferred path: Scrapling fetch adapter ──────────────────────────────
    if fetcher.is_active() and not fetcher.should_bypass(source):
        result = await fetcher.fetch_page(url, source=source)
        if result.ok and result.html is not None:
            logger.debug(
                "fetched %s via scrapling:%s", url, result.engine_used
            )
            return httpx.Response(status_code=result.status, html=result.html)
        logger.debug(
            "scrapling fetch unusable for %s (%s); falling back to httpx",
            url, result.reason,
        )

    # ── Fallback path: built-in httpx GET (unchanged) ────────────────────────
    try:
        resp = await client.get(url, follow_redirects=True, timeout=20.0)
    except httpx.HTTPError as exc:
        logger.warning("HTTP error fetching %s: %s", url, exc)
        return None

    if resp.status_code >= 400:
        if resp.status_code in (429, 503):
            logger.warning("Rate-limited (%s) fetching %s", resp.status_code, url)
        else:
            logger.warning("Bad status %s fetching %s", resp.status_code, url)
        return None

    return resp


class Scraper(ABC):
    name: ClassVar[str]

    @abstractmethod
    async def fetch(self, location: str) -> list[Job]: ...
