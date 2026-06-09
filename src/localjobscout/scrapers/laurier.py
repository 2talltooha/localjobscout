from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from localjobscout.db import Job, make_job_id
from localjobscout.scrapers.base import USER_AGENT, Scraper, polite_get

logger = logging.getLogger(__name__)

_BASE = "https://careers.wlu.ca"
_CATEGORY_PATHS = (
    "/go/Staff-and-Management-Positions/505147/",
    "/go/Academic-Positions/505047/",
)
_RESULTS_PER_PAGE = 25
_MAX_LISTINGS = 100


class LaurierScraper(Scraper):
    name = "laurier"

    def __init__(self, max_pages: int = 3) -> None:
        self._max_pages = max_pages

    async def fetch(self, location: str) -> list[Job]:
        jobs: list[Job] = []
        seen_urls: set[str] = set()

        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}
        ) as client:
            for category in _CATEGORY_PATHS:
                if len(jobs) >= _MAX_LISTINGS:
                    break
                await self._fetch_category(
                    client, category, jobs, seen_urls
                )

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

            location_el = row.select_one(
                "td.colLocation span.jobLocation"
            ) or row.select_one("span.jobLocation")
            location = (
                location_el.get_text(strip=True) if location_el else ""
            )

            facility_el = row.select_one(
                "td.colFacility span.jobFacility"
            ) or row.select_one("span.jobFacility")
            facility = (
                facility_el.get_text(strip=True) if facility_el else ""
            )

            description_parts: list[str] = []
            if facility:
                description_parts.append(f"Department: {facility}")
            if location:
                description_parts.append(f"Location: {location}")
            description = "\n".join(description_parts)

            return Job(
                id=make_job_id("laurier", detail_url),
                source="laurier",
                title=title,
                company="Wilfrid Laurier University",
                location=location,
                url=detail_url,
                description=description,
                posted_at=None,
                first_seen=datetime.now(UTC).isoformat(),
                score=None,
                notified=False,
            )
        except Exception:
            logger.exception("Failed to parse laurier row, skipping")
            return None

    @staticmethod
    def _description_from_detail_html(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        desc_el = soup.select_one(
            'span[itemprop="description"] span.jobdescription'
        ) or soup.select_one("span.jobdescription")
        if desc_el is None or not isinstance(desc_el, Tag):
            return ""
        text = " ".join(desc_el.get_text(" ", strip=True).split())
        return text if text else ""

    async def _enrich_description(
        self, client: httpx.AsyncClient, job: Job
    ) -> None:
        detail = await polite_get(client, job.url)
        if detail is None:
            return
        body = self._description_from_detail_html(detail.text)
        if body:
            job.description = body
