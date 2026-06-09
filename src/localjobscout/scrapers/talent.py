"""Talent.com job-board scraper (ca.talent.com).

A large aggregator with strong healthcare/clinical coverage. Unlike the hospital
ATS portals (which migrated to JS-rendered iCIMS/Workday or dead domains),
Talent.com serves job cards in static HTML, so plain httpx + BeautifulSoup
works — no Playwright, no Cloudflare.

Card markup uses build-hashed CSS classes (e.g. ``JobCard_title__X32Qk``) that
change between deploys, so we select on stable hooks instead:
- cards:    ``article[data-testid="job-card-unified"]``
- title:    ``[class*="JobCard_title"]``
- company:  ``[class*="JobCard_company"]``
- location: ``[class*="JobCard_location"]``
- link:     ``a[href^="/view"]`` → https://ca.talent.com/view?id=...
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import quote_plus, urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from localjobscout.db import Job, make_job_id
from localjobscout.scrapers.base import USER_AGENT, Scraper, polite_get

logger = logging.getLogger(__name__)

_BASE = "https://ca.talent.com"
_MAX_LISTINGS = 100
# Cap full-description fetches per run — card snippets are too thin for the
# relevance/credential filters, but fetching every /view page would slow scans.
_MAX_ENRICH = 40


class TalentScraper(Scraper):
    name = "talent"

    def __init__(
        self, query: str = "healthcare", max_pages: int = 3, location: str = ""
    ) -> None:
        self._query = query or "healthcare"
        self._max_pages = max_pages
        self._location_override = location

    async def fetch(self, location: str) -> list[Job]:
        loc = self._location_override or location
        jobs: list[Job] = []
        seen: set[str] = set()

        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            for page in range(1, self._max_pages + 1):
                if len(jobs) >= _MAX_LISTINGS:
                    break
                url = (
                    f"{_BASE}/jobs?k={quote_plus(self._query)}"
                    f"&l={quote_plus(loc)}&p={page}"
                )
                resp = await polite_get(client, url)
                if resp is None:
                    break
                soup = BeautifulSoup(resp.text, "html.parser")
                page_jobs = self._parse_page(soup, seen)
                if not page_jobs:
                    break
                jobs.extend(page_jobs[: _MAX_LISTINGS - len(jobs)])

            # Replace thin card snippets with the full posting body so the
            # downstream relevance/credential filters judge on real content.
            for job in jobs[:_MAX_ENRICH]:
                await self._enrich_description(client, job)

        return jobs

    async def _enrich_description(
        self, client: httpx.AsyncClient, job: Job
    ) -> None:
        detail = await polite_get(client, job.url)
        if detail is None:
            return
        soup = BeautifulSoup(detail.text, "html.parser")
        body = (
            soup.select_one('[class*="jobDescription"]')
            or soup.select_one('[class*="JobDescription"]')
            or soup.select_one('[class*="description"]')
            or soup.select_one("main")
        )
        if isinstance(body, Tag):
            text = " ".join(body.get_text(" ", strip=True).split())
            if len(text) > len(job.description):
                job.description = text[:8000]

    def _parse_page(self, soup: BeautifulSoup, seen: set[str]) -> list[Job]:
        results: list[Job] = []
        cards = soup.select('article[data-testid="job-card-unified"]')
        for card in cards:
            job = self._parse_card(card, seen)
            if job:
                results.append(job)
        if not cards:
            logger.debug("talent: no job cards matched on page")
        return results

    def _parse_card(self, card: Tag, seen: set[str]) -> Job | None:
        try:
            link = card.find("a", href=True)
            if not isinstance(link, Tag):
                return None
            href = link.get("href")
            if not isinstance(href, str) or "/view" not in href:
                return None
            detail_url = urljoin(_BASE, href)
            if detail_url in seen:
                return None
            seen.add(detail_url)

            title = self._text(card, '[class*="JobCard_title"]')
            if not title or len(title) < 3:
                return None
            company = self._text(card, '[class*="JobCard_company"]') or "Unknown"
            location = self._text(card, '[class*="JobCard_location"]') or ""
            snippet = self._text(card, '[class*="JobCard_description"]') or ""

            return Job(
                id=make_job_id("talent", detail_url),
                source="talent",
                title=title,
                company=company,
                location=location,
                url=detail_url,
                description=snippet or f"{title} at {company}. {location}",
                posted_at=None,
                first_seen=datetime.now(UTC).isoformat(),
                score=None,
                notified=False,
            )
        except Exception:
            logger.exception("talent: failed to parse card, skipping")
            return None

    @staticmethod
    def _text(node: Tag, selector: str) -> str:
        el = node.select_one(selector)
        return el.get_text(strip=True) if el else ""
