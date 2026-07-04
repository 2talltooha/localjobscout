from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import ClassVar

import httpx

from localjobscout.db import Job
from localjobscout.scrapers import fetcher, politeness

logger = logging.getLogger(__name__)

USER_AGENT = politeness.USER_AGENT

# Real-browser UA for hosts that reject the identifying USER_AGENT above
# (Workday CXS, Playwright-driven Indeed/LinkedIn, liveness checks). Single
# source of truth — was duplicated verbatim across 5 modules.
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


async def polite_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    delay_seconds: float = 2.0,
    source: str | None = None,
) -> httpx.Response | None:
    """Fetch a URL politely (robots + per-host rate limit), preferring the
    Scrapling adapter.

    The robots check and rate limiter live in ``scrapers.politeness`` and are
    shared with ``fetcher.fetch_selector`` — every fetch, on either engine,
    respects the same per-host minimum interval instead of a flat pre-request
    sleep only this function knew about.

    When the fetch adapter is active it handles the GET (anti-bot resilient)
    and the result is wrapped in an ``httpx.Response`` so callers are
    unchanged. If the adapter is inactive or fails, this falls back to the
    original httpx GET — identical to pre-integration behaviour.
    """
    if not await politeness.can_fetch(url, USER_AGENT):
        logger.warning("robots.txt disallows %s for %s", url, USER_AGENT)
        return None

    await politeness.throttle(url, delay_seconds)

    # ── Preferred path: Scrapling fetch adapter ──────────────────────────────
    if fetcher.is_active() and not fetcher.should_bypass(source):
        result = await fetcher.fetch_page(url, source=source)
        if result.ok and result.html is not None:
            logger.debug(
                "fetched %s via scrapling:%s", url, result.engine_used
            )
            return httpx.Response(
                status_code=result.status,
                html=result.html,
                request=httpx.Request("GET", url),
            )
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
