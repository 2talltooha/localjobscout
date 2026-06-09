from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import quote_plus

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

try:
    from playwright_stealth import Stealth
    _STEALTH_AVAILABLE = True
except ImportError:
    _STEALTH_AVAILABLE = False

logger = logging.getLogger(__name__)

_BASE_URL = "https://ca.indeed.com/jobs"
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_PAGE_SIZE = 10
_PAGE_TIMEOUT_MS = 20_000
_BETWEEN_PAGES_SLEEP_S = 3.0
_BETWEEN_DESC_SLEEP_S = 1.5
# Max descriptions fetched per run to keep total time bounded
_MAX_DESC_FETCHES = 20

# CSS selectors tried in order when extracting description from job detail page
_DESCRIPTION_SELECTORS: tuple[str, ...] = (
    "div#jobDescriptionText",
    "div.jobsearch-jobDescriptionText",
    "div[data-testid='jobDescriptionText']",
    "div.jobDescription",
)

# Cloudflare's interstitial page; if we see this in <title> the JS challenge
# hasn't resolved and we have to bail.
_CLOUDFLARE_TITLE_HINTS = (
    "Just a moment",
    "Attention Required",
    "Additional Verification Required",
)

def extract_description_from_html(html: str) -> str:
    """Parse an Indeed job detail page and return the description text.

    Tries multiple CSS selectors in order; returns the first match with
    at least 30 characters. Returns "" if nothing usable is found.
    Pure function — no Playwright dependency, unit-testable with saved HTML.
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


_EXTRACT_JS = """
() => {
  const cards = document.querySelectorAll(
    'div.job_seen_beacon, [data-testid="slider_item"]'
  );
  const out = [];
  for (const card of cards) {
    const link = card.querySelector(
      'a.jcs-JobTitle, h2.jobTitle a, a[data-jk]'
    );
    if (!link) continue;
    const href = link.href || '';
    let jk = link.getAttribute('data-jk') || '';
    if (!jk) {
      const m = href.match(/[?&]jk=([^&]+)/);
      if (m) jk = m[1];
    }
    if (!jk) continue;

    const title = (
      link.querySelector('span[title]')?.getAttribute('title')
      || link.innerText
      || ''
    ).trim();
    const company = (
      card.querySelector(
        '[data-testid="company-name"], span.companyName'
      )?.innerText || ''
    ).trim();
    const location = (
      card.querySelector(
        '[data-testid="text-location"], div.companyLocation'
      )?.innerText || ''
    ).trim();
    const snippet = (
      card.querySelector(
        '[data-testid="jobsnippet_footer"] ul li, div.snippet p, '
        + 'div.job-snippet'
      )?.innerText || ''
    ).trim();

    if (!title) continue;
    out.push({ jk, title, company, location, snippet });
  }
  return out;
}
"""


class IndeedPlaywrightScraper(Scraper):
    """Scrape Indeed Canada job listings via a real Chromium.

    Indeed sits behind Cloudflare's anti-bot challenge, which usually blocks
    headless browsers despite `--disable-blink-features=AutomationControlled`.
    If the challenge fires we detect it via the document title and return [],
    so the scraper degrades gracefully instead of throwing.
    """

    name = "indeed"

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
                    if _STEALTH_AVAILABLE:
                        try:
                            await Stealth().apply_stealth_async(context)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "indeed: playwright-stealth apply failed: %s",
                                exc,
                            )

                    for page_idx in range(self._max_pages):
                        start = page_idx * _PAGE_SIZE
                        url = (
                            f"{_BASE_URL}?q={quote_plus(self._query)}"
                            f"&l={quote_plus(location)}&start={start}"
                        )
                        try:
                            await page.goto(url, timeout=_PAGE_TIMEOUT_MS)
                        except PlaywrightTimeoutError:
                            logger.warning(
                                "indeed: navigation timeout on page %d "
                                "(start=%d); returning %d jobs collected so "
                                "far", page_idx, start, len(jobs),
                            )
                            break

                        title = (await page.title()) or ""
                        if any(hint in title for hint in _CLOUDFLARE_TITLE_HINTS):
                            logger.warning(
                                "indeed: Cloudflare challenge detected "
                                "(title=%r); aborting scraper. Returning %d "
                                "jobs.", title, len(jobs),
                            )
                            break

                        try:
                            await page.wait_for_selector(
                                'div.job_seen_beacon, [data-testid="slider_item"]',
                                timeout=_PAGE_TIMEOUT_MS,
                            )
                        except PlaywrightTimeoutError:
                            logger.warning(
                                "indeed: no job cards found on page %d "
                                "(start=%d); returning %d jobs collected so "
                                "far", page_idx, start, len(jobs),
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

                    # ── Phase 2: fetch full descriptions ──────────────────
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
                "indeed: playwright error after %d jobs: %s", len(jobs), exc
            )
        except Exception:
            logger.exception(
                "indeed: unexpected error after %d jobs", len(jobs)
            )

        return jobs

    @staticmethod
    async def _fetch_description(page: Any, url: str) -> str:
        """Navigate to the Indeed job detail page and extract the description."""
        try:
            await page.goto(url, timeout=_PAGE_TIMEOUT_MS,
                            wait_until="domcontentloaded")
            html: str = await page.content()
            return extract_description_from_html(html)
        except Exception as exc:  # noqa: BLE001
            logger.debug("indeed: desc fetch failed %s: %s", url[:60], exc)
            return ""

    @staticmethod
    def _row_to_job(entry: dict[str, Any]) -> Job | None:
        jk = entry.get("jk") or ""
        title = entry.get("title") or ""
        if not jk or not title:
            return None
        if not isinstance(jk, str) or not isinstance(title, str):
            return None

        url = f"https://ca.indeed.com/viewjob?jk={jk}"
        company = entry.get("company") or ""
        location = entry.get("location") or ""
        snippet = entry.get("snippet") or ""

        return Job(
            id=make_job_id("indeed", url),
            source="indeed",
            title=title,
            company=company if isinstance(company, str) else "",
            location=location if isinstance(location, str) else "",
            url=url,
            description=snippet if isinstance(snippet, str) else "",
            posted_at=None,
            first_seen=datetime.now(UTC).isoformat(),
            score=None,
            notified=False,
        )
