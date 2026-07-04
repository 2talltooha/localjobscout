"""Shared politeness layer: robots.txt cache + per-host rate limiting.

Both fetch paths — the legacy httpx GET (``scrapers/base.py``) and the
Scrapling adaptive path (``scrapers/fetcher.py``) — route through this module
so every request, regardless of engine, respects robots.txt and a minimum
per-host request interval. Without this, the 10 adaptive scrapers (which call
``fetcher.fetch_selector`` directly) skipped robots/delay entirely, and 8
concurrent category-query instances of the same board hammered it with zero
inter-request spacing.
"""
from __future__ import annotations

import asyncio
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

USER_AGENT = "LocalJobScout/0.1 (personal use)"

_ROBOTS_TTL = 24 * 3600.0

# host -> (parser, fetched_at monotonic seconds)
_robots_cache: dict[str, tuple[RobotFileParser, float]] = {}

# host -> asyncio.Lock, serializes throttle() for that host so concurrent
# requests to the same board (e.g. 8 category-query scraper instances) can't
# race past the minimum interval check.
_host_locks: dict[str, asyncio.Lock] = {}
# host -> monotonic time of the last request allowed through.
_host_last_request: dict[str, float] = {}


def _get_host_lock(host: str) -> asyncio.Lock:
    lock = _host_locks.get(host)
    if lock is None:
        lock = asyncio.Lock()
        _host_locks[host] = lock
    return lock


async def _fetch_robots(host: str) -> RobotFileParser:
    parser = RobotFileParser()
    robots_url = f"https://{host}/robots.txt"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(robots_url, follow_redirects=True, timeout=10.0)
        if resp.status_code < 400:
            parser.parse(resp.text.splitlines())
        else:
            # Treat inaccessible robots.txt as allowing everything.
            parser.allow_all = True  # type: ignore[attr-defined]
    except httpx.HTTPError:
        parser.allow_all = True  # type: ignore[attr-defined]
    return parser


async def get_robots(host: str) -> RobotFileParser:
    """Cached robots.txt parser for *host*, refreshed after 24h."""
    now = time.monotonic()
    cached = _robots_cache.get(host)
    if cached is not None and now - cached[1] < _ROBOTS_TTL:
        return cached[0]
    parser = await _fetch_robots(host)
    _robots_cache[host] = (parser, now)
    return parser


async def can_fetch(url: str, user_agent: str = USER_AGENT) -> bool:
    host = urlparse(url).netloc
    robots = await get_robots(host)
    return robots.can_fetch(user_agent, url)


async def throttle(url: str, min_interval: float) -> None:
    """Block until at least *min_interval* seconds have passed since the last
    request to this URL's host — across every caller/engine, not per-call.

    Unlike a flat pre-request sleep, this only waits when a *previous*
    request to the same host was recent; the first request to a host never
    waits, and unrelated hosts never block each other.
    """
    if min_interval <= 0:
        return
    host = urlparse(url).netloc
    lock = _get_host_lock(host)
    async with lock:
        now = time.monotonic()
        last = _host_last_request.get(host)
        if last is not None:
            wait = min_interval - (now - last)
            if wait > 0:
                await asyncio.sleep(wait)
        _host_last_request[host] = time.monotonic()


def reset() -> None:
    """Clear all cached state (tests only)."""
    _robots_cache.clear()
    _host_locks.clear()
    _host_last_request.clear()
