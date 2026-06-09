from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import ClassVar
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from localjobscout.db import Job

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
) -> httpx.Response | None:
    parsed = urlparse(url)
    host = parsed.netloc

    robots = await _get_robots(client, host)
    if not robots.can_fetch(USER_AGENT, url):
        logger.warning("robots.txt disallows %s for %s", url, USER_AGENT)
        return None

    await asyncio.sleep(delay_seconds)

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
