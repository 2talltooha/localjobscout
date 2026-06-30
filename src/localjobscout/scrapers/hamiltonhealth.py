"""Hamilton Health Sciences careers scraper.

HHS uses a Taleo-based portal at careers.hamiltonhealthsciences.ca — the
same platform as Laurier (careers.wlu.ca), so the CSS selectors match.
Covers Hamilton General, Juravinski, McMaster Children's, and St. Peter's.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from localjobscout.db import Job, make_job_id
from localjobscout.scrapers import fetcher
from localjobscout.scrapers.adaptive import all_matches, first
from localjobscout.scrapers.base import USER_AGENT, Scraper, polite_get

logger = logging.getLogger(__name__)

_BASE = "https://careers.hamiltonhealthsciences.ca"
_CATEGORY_PATHS = (
    "/go/All-Current-Openings/5447500/",
    "/go/Allied-Health-Clinical/8773300/",
)
_RESULTS_PER_PAGE = 25
_MAX_LISTINGS = 100


# ── Adaptive (self-healing) extraction ───────────────────────────────────────
def extract_rows_adaptive(selector: Any) -> list[dict[str, str]]:
    """Pull job rows from a hamiltonhealth (Taleo) listing Selector."""
    rows = all_matches(selector, "tr.data-row", identifier="hamiltonhealth_row_list")
    out: list[dict[str, str]] = []
    for row in rows:
        link = first(row, "a.jobTitle-link")
        if link is None:
            continue
        href = link.attrib.get("href")
        title = str(link.text or "").strip()
        if not href or not title:
            continue
        location_el = first(row, "span.jobLocation")
        facility_el = first(row, "span.jobFacility")
        out.append(
            {
                "href": href,
                "title": title,
                "location": str(location_el.text or "").strip()
                if location_el is not None
                else "Hamilton, ON",
                "facility": str(facility_el.text or "").strip()
                if facility_el is not None
                else "",
            }
        )
    return out


def extract_description_adaptive(selector: Any) -> str:
    el = first(
        selector,
        'span[itemprop="description"] span.jobdescription',
        identifier="hamiltonhealth_job_desc",
    )
    if el is None:
        el = first(selector, "span.jobdescription")
    if el is None:
        return ""
    return " ".join(el.get_all_text().split()).strip()


class HamiltonHealthScraper(Scraper):
    name = "hamiltonhealth"

    def __init__(self, max_pages: int = 3) -> None:
        self._max_pages = max_pages

    async def fetch(self, location: str) -> list[Job]:
        if fetcher.adaptive_enabled():
            try:
                jobs = await self._fetch_adaptive(location)
                if jobs:
                    return jobs
                logger.debug("hamiltonhealth: adaptive yielded 0; using legacy path")
            except Exception:
                logger.exception("hamiltonhealth: adaptive path failed; using legacy")
        return await self._fetch_legacy(location)

    async def _fetch_adaptive(self, location: str) -> list[Job]:
        jobs: list[Job] = []
        seen_urls: set[str] = set()
        for category in _CATEGORY_PATHS:
            if len(jobs) >= _MAX_LISTINGS:
                break
            for page in range(self._max_pages):
                if len(jobs) >= _MAX_LISTINGS:
                    break
                startrow = page * _RESULTS_PER_PAGE
                url = urljoin(_BASE, f"{category}?startrow={startrow}")
                selector = await fetcher.fetch_selector(url, source="hamiltonhealth")
                if selector is None:
                    return jobs
                rows = extract_rows_adaptive(selector)
                if not rows:
                    break
                page_added = 0
                for row in rows:
                    if len(jobs) >= _MAX_LISTINGS:
                        break
                    detail_url = urljoin(url, row["href"])
                    if detail_url in seen_urls:
                        continue
                    seen_urls.add(detail_url)
                    description = f"Location: {row['location']}"
                    if row["facility"]:
                        description = f"Facility: {row['facility']}\n{description}"
                    detail_sel = await fetcher.fetch_selector(
                        detail_url, source="hamiltonhealth"
                    )
                    if detail_sel is not None:
                        body = extract_description_adaptive(detail_sel)
                        if body:
                            description = body
                    jobs.append(
                        Job(
                            id=make_job_id("hamiltonhealth", detail_url),
                            source="hamiltonhealth",
                            title=row["title"],
                            company="Hamilton Health Sciences",
                            location=row["location"],
                            url=detail_url,
                            description=description,
                            posted_at=None,
                            first_seen=datetime.now(UTC).isoformat(),
                            score=None,
                            notified=False,
                        )
                    )
                    page_added += 1
                if page_added == 0:
                    break
        return jobs

    async def _fetch_legacy(self, location: str) -> list[Job]:
        jobs: list[Job] = []
        seen_urls: set[str] = set()

        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}
        ) as client:
            for category in _CATEGORY_PATHS:
                if len(jobs) >= _MAX_LISTINGS:
                    break
                await self._fetch_category(client, category, jobs, seen_urls)

        return jobs

    async def _fetch_category(
        self,
        client: httpx.AsyncClient,
        category_path: str,
        jobs: list[Job],
        seen_urls: set[str],
    ) -> None:
        for page in range(self._max_pages):
            if len(jobs) >= _MAX_LISTINGS:
                return
            startrow = page * _RESULTS_PER_PAGE
            url = urljoin(_BASE, f"{category_path}?startrow={startrow}")
            resp = await polite_get(client, url)
            if resp is None:
                return
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("tr.data-row")
            if not rows:
                return
            page_added = 0
            for row in rows:
                if len(jobs) >= _MAX_LISTINGS:
                    return
                job = self._parse_row(row, url)
                if job is None:
                    continue
                if job.url in seen_urls:
                    continue
                seen_urls.add(job.url)
                await self._enrich_description(client, job)
                jobs.append(job)
                page_added += 1
            if page_added == 0:
                return

    def _parse_row(self, row: Tag, page_url: str) -> Job | None:
        try:
            link = row.select_one("a.jobTitle-link")
            if link is None:
                return None
            href = link.get("href")
            if not isinstance(href, str) or not href:
                return None
            title = link.get_text(strip=True)
            if not title:
                return None
            detail_url = urljoin(page_url, href)

            location_el = row.select_one("span.jobLocation")
            location = (
                location_el.get_text(strip=True) if location_el else "Hamilton, ON"
            )

            facility_el = row.select_one("span.jobFacility")
            facility = facility_el.get_text(strip=True) if facility_el else ""

            description = f"Location: {location}"
            if facility:
                description = f"Facility: {facility}\n{description}"

            return Job(
                id=make_job_id("hamiltonhealth", detail_url),
                source="hamiltonhealth",
                title=title,
                company="Hamilton Health Sciences",
                location=location,
                url=detail_url,
                description=description,
                posted_at=None,
                first_seen=datetime.now(UTC).isoformat(),
                score=None,
                notified=False,
            )
        except Exception:
            logger.exception("Failed to parse hamiltonhealth row, skipping")
            return None

    @staticmethod
    def _description_from_detail(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        desc_el = (
            soup.select_one('span[itemprop="description"] span.jobdescription')
            or soup.select_one("span.jobdescription")
        )
        if desc_el is None or not isinstance(desc_el, Tag):
            return ""
        return " ".join(desc_el.get_text(" ", strip=True).split())

    async def _enrich_description(
        self, client: httpx.AsyncClient, job: Job
    ) -> None:
        detail = await polite_get(client, job.url)
        if detail is None:
            return
        body = self._description_from_detail(detail.text)
        if body:
            job.description = body
