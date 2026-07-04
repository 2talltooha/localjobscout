from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from localjobscout.db import Job, make_job_id
from localjobscout.scrapers import fetcher
from localjobscout.scrapers.adaptive import all_matches, first, taleo_description
from localjobscout.scrapers.base import USER_AGENT, Scraper, polite_get

logger = logging.getLogger(__name__)

_BASE = "https://careers.wlu.ca"
_CATEGORY_PATHS = (
    "/go/Staff-and-Management-Positions/505147/",
    "/go/Academic-Positions/505047/",
)
_RESULTS_PER_PAGE = 25
_MAX_LISTINGS = 100


# ── Adaptive (self-healing) extraction ───────────────────────────────────────
def extract_rows_adaptive(selector: Any) -> list[dict[str, str]]:
    """Pull job rows from a laurier (Taleo) listing Selector via adaptive selectors."""
    rows = all_matches(selector, "tr.data-row", identifier="laurier_row_list")
    out: list[dict[str, str]] = []
    for row in rows:
        link = first(row, "a.jobTitle-link")
        if link is None:
            continue
        href = link.attrib.get("href")
        title = str(link.text or "").strip()
        if not href or not title:
            continue
        location_el = first(row, "td.colLocation span.jobLocation") or first(
            row, "span.jobLocation"
        )
        facility_el = first(row, "td.colFacility span.jobFacility") or first(
            row, "span.jobFacility"
        )
        out.append(
            {
                "href": href,
                "title": title,
                "location": str(location_el.text or "").strip()
                if location_el is not None
                else "",
                "facility": str(facility_el.text or "").strip()
                if facility_el is not None
                else "",
            }
        )
    return out


def extract_description_adaptive(selector: Any) -> str:
    return taleo_description(selector, "laurier_job_desc")


class LaurierScraper(Scraper):
    name = "laurier"

    def __init__(
        self, max_pages: int = 3, known_ids: frozenset[str] = frozenset()
    ) -> None:
        self._max_pages = max_pages
        self._known_ids = known_ids

    async def fetch(self, location: str) -> list[Job]:
        if fetcher.adaptive_enabled():
            try:
                jobs = await self._fetch_adaptive(location)
                if jobs:
                    return jobs
                logger.debug("laurier: adaptive yielded 0; using legacy path")
            except Exception:
                logger.exception("laurier: adaptive path failed; using legacy")
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
                selector = await fetcher.fetch_selector(url, source="laurier")
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
                    job_id = make_job_id("laurier", detail_url)
                    description_parts: list[str] = []
                    if row["facility"]:
                        description_parts.append(f"Department: {row['facility']}")
                    if row["location"]:
                        description_parts.append(f"Location: {row['location']}")
                    description = "\n".join(description_parts)
                    if job_id not in self._known_ids:
                        detail_sel = await fetcher.fetch_selector(
                            detail_url, source="laurier"
                        )
                        if detail_sel is not None:
                            body = extract_description_adaptive(detail_sel)
                            if body:
                                description = body
                    jobs.append(
                        Job(
                            id=job_id,
                            source="laurier",
                            title=row["title"],
                            company="Wilfrid Laurier University",
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
