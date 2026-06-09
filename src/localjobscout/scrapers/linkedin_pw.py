from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import quote_plus, urlparse

from bs4 import BeautifulSoup
from playwright.async_api import (
    Error as PlaywrightError,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
)
from playwright.async_api import (
    async_playwright,
)

from localjobscout.db import Job, make_job_id
from localjobscout.scrapers.base import Scraper

logger = logging.getLogger(__name__)

_BASE_URL = (
    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
)
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_PAGE_SIZE = 10
_PAGE_TIMEOUT_MS = 15_000
_BETWEEN_PAGES_SLEEP_S = 2.0
_BETWEEN_DESC_SLEEP_S = 1.5
# Max descriptions fetched per scrape run to keep total time reasonable
_MAX_DESC_FETCHES = 20

_JOB_ID_RE = re.compile(r"(\d+)/?$")

# CSS selectors tried in order when extracting description from job page HTML
_DESCRIPTION_SELECTORS: tuple[str, ...] = (
    "div.show-more-less-html__markup",
    "div.description__text",
    "div.jobs-description-content__text",
    "section.description",
)

_EXTRACT_JS = """
() => {
  const cards = document.querySelectorAll('.base-card, li');
  const out = [];
  for (const card of cards) {
    const link = card.querySelector('a.base-card__full-link');
    if (!link) continue;
    const url = link.href || '';
    const title = (card.querySelector('.base-search-card__title')?.innerText
                   || '').trim();
    const company = (card.querySelector('.base-search-card__subtitle')?.innerText
                     || '').trim();
    const location = (card.querySelector('.job-search-card__location')?.innerText
                      || '').trim();
    const time = card.querySelector('time[datetime]');
    const postedAt = time ? (time.getAttribute('datetime') || '') : '';
    if (!url || !title) continue;
    out.push({ url, title, company, location, posted_at: postedAt });
  }
  return out;
}
"""


def _normalise_job_url(href: str) -> str:
    """Canonicalise a LinkedIn job URL to https://www.linkedin.com/jobs/view/<id>/."""
    parsed = urlparse(href)
    m = _JOB_ID_RE.search(parsed.path.rstrip("/"))
    if not m:
        return href
    job_id = m.group(1)
    return f"https://www.linkedin.com/jobs/view/{job_id}/"


def extract_description_from_html(html: str) -> str:
    """Parse a LinkedIn public job page HTML and return the description text.

    Tries multiple CSS selectors in order; returns the first match with
    at least 30 characters. Returns "" if nothing usable is found.
    This is a pure function with no Playwright dependency — unit-testable
    directly with a saved HTML fixture.
    """
    if not html or not html.strip():
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for selector in _DESCRIPTION_SELECTORS:
        el = soup.select_one(selector)
        if el:
            text = el.get_text(separator=" ", strip=True)
            if len(text) >= 30:
                return text
    return ""


class LinkedInPlaywrightScraper(Scraper):
    """Scrape LinkedIn's public guest-jobs endpoint with a real Chromium.

    Phase 1: Fetch job cards (url, title, company, location) via the guest
    pagination API — no login required.

    Phase 2: For each card URL, navigate to the public job page and extract
    the full description text.  Capped at ``_MAX_DESC_FETCHES`` to keep
    total scrape time bounded.  Any failure leaves description empty.
    """

    name = "linkedin"

    def __init__(self, query: str = "", max_pages: int = 3) -> None:
        self._query = query
        self._max_pages = max_pages

    async def fetch(self, location: str) -> list[Job]:
        jobs: list[Job] = []
        seen_urls: set[str] = set()

        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                try:
                    context = await browser.new_context(
                        user_agent=_BROWSER_USER_AGENT,
                    )
                    page = await context.new_page()

                    # ── Phase 1: collect job cards ────────────────────────
                    for page_idx in range(self._max_pages):
                        start = page_idx * _PAGE_SIZE
                        url = (
                            f"{_BASE_URL}?keywords={quote_plus(self._query)}"
                            f"&location={quote_plus(location)}&start={start}"
                        )
                        try:
                            await page.goto(url, timeout=_PAGE_TIMEOUT_MS)
                            await page.wait_for_selector(
                                ".base-card", timeout=_PAGE_TIMEOUT_MS
                            )
                        except PlaywrightTimeoutError:
                            logger.warning(
                                "linkedin: timeout on card page %d; "
                                "returning %d jobs so far",
                                page_idx, len(jobs),
                            )
                            break

                        raw = cast(
                            list[dict[str, Any]],
                            await page.evaluate(_EXTRACT_JS),
                        )
                        page_added = 0
                        for entry in raw:
                            job = self._row_to_job(entry)
                            if job is None:
                                continue
                            if job.url in seen_urls:
                                continue
                            seen_urls.add(job.url)
                            jobs.append(job)
                            page_added += 1

                        if page_added == 0:
                            break

                        await asyncio.sleep(_BETWEEN_PAGES_SLEEP_S)

                    # ── Phase 2: fetch descriptions ───────────────────────
                    fetched = 0
                    for job in jobs:
                        if fetched >= _MAX_DESC_FETCHES:
                            break
                        desc = await self._fetch_description(page, job.url)
                        if desc:
                            job.description = desc
                        fetched += 1
                        await asyncio.sleep(_BETWEEN_DESC_SLEEP_S)

                finally:
                    await browser.close()
        except PlaywrightError as exc:
            logger.warning(
                "linkedin: playwright error after %d jobs: %s", len(jobs), exc
            )
        except Exception:
            logger.exception(
                "linkedin: unexpected error after %d jobs", len(jobs)
            )

        return jobs

    @staticmethod
    async def _fetch_description(page: Any, url: str) -> str:
        """Navigate to the public job page and extract the description."""
        try:
            await page.goto(url, timeout=_PAGE_TIMEOUT_MS,
                            wait_until="domcontentloaded")
            html: str = await page.content()
            return extract_description_from_html(html)
        except Exception as exc:
            logger.debug("linkedin: desc fetch failed %s: %s", url[:60], exc)
            return ""

    @staticmethod
    def _row_to_job(entry: dict[str, Any]) -> Job | None:
        raw_url = entry.get("url") or ""
        title = entry.get("title") or ""
        if not raw_url or not title:
            return None
        if not isinstance(raw_url, str) or not isinstance(title, str):
            return None

        url = _normalise_job_url(raw_url)
        company = entry.get("company") or ""
        location = entry.get("location") or ""
        posted_at_raw = entry.get("posted_at") or ""
        posted_at: str | None = (
            posted_at_raw if isinstance(posted_at_raw, str) and posted_at_raw
            else None
        )

        return Job(
            id=make_job_id("linkedin", url),
            source="linkedin",
            title=title,
            company=company if isinstance(company, str) else "",
            location=location if isinstance(location, str) else "",
            url=url,
            description="",
            posted_at=posted_at,
            first_seen=datetime.now(UTC).isoformat(),
            score=None,
            notified=False,
        )
