"""St. Mary's General Hospital careers scraper (Kitchener, ON).

St. Mary's is a Catholic hospital serving the Waterloo Region. Their
careers page lists current openings. If the portal structure changes,
update _LISTING_URL and CSS selectors below.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from localjobscout.db import Job, make_job_id
from localjobscout.scrapers.base import USER_AGENT, Scraper, polite_get

logger = logging.getLogger(__name__)

_LISTING_URL = "https://www.smgh.ca/about-st-marys/careers"
_MAX_LISTINGS = 100


class StMarysScraper(Scraper):
    name = "stmarys"

    def __init__(self, max_pages: int = 2) -> None:
        self._max_pages = max_pages

    async def fetch(self, location: str) -> list[Job]:
        jobs: list[Job] = []
        seen_urls: set[str] = set()

        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            for page in range(self._max_pages):
                if len(jobs) >= _MAX_LISTINGS:
                    break
                url = _LISTING_URL if page == 0 else f"{_LISTING_URL}?page={page}"
                resp = await polite_get(client, url)
                if resp is None:
                    break
                soup = BeautifulSoup(resp.text, "html.parser")
                page_jobs = self._parse_page(soup, url, seen_urls)
                if not page_jobs:
                    break
                for job in page_jobs:
                    if len(jobs) >= _MAX_LISTINGS:
                        break
                    await self._enrich_description(client, job)
                    jobs.append(job)

        return jobs

    def _parse_page(
        self, soup: BeautifulSoup, base_url: str, seen_urls: set[str]
    ) -> list[Job]:
        results: list[Job] = []
        candidates = (
            soup.select("li.views-row")
            or soup.select("div.view-content > div")
            or soup.select("article.node--type-job")
            or soup.select("table.career-table tbody tr")
            or soup.select(".job-posting")
        )
        for item in candidates:
            job = self._parse_item(item, base_url, seen_urls)
            if job:
                results.append(job)
        if not results:
            logger.debug(
                "stmarys: no job items matched known selectors on %s", base_url
            )
        return results

    def _parse_item(
        self, item: Tag, base_url: str, seen_urls: set[str]
    ) -> Job | None:
        try:
            link = item.find("a")
            if not isinstance(link, Tag):
                return None
            href = link.get("href")
            if not isinstance(href, str) or not href:
                return None
            detail_url = urljoin(base_url, href)
            if detail_url in seen_urls:
                return None
            seen_urls.add(detail_url)
            title = link.get_text(strip=True)
            if not title or len(title) < 4:
                return None
            description = "Location: Kitchener, ON"
            dept_el = item.select_one(".department, .field--name-field-department")
            if dept_el:
                dept_text = dept_el.get_text(strip=True)
                description = f"Department: {dept_text}\n{description}"
            return Job(
                id=make_job_id("stmarys", detail_url),
                source="stmarys",
                title=title,
                company="St. Mary's General Hospital",
                location="Kitchener, ON",
                url=detail_url,
                description=description,
                posted_at=None,
                first_seen=datetime.now(UTC).isoformat(),
                score=None,
                notified=False,
            )
        except Exception:
            logger.exception("Failed to parse stmarys item, skipping")
            return None

    async def _enrich_description(
        self, client: httpx.AsyncClient, job: Job
    ) -> None:
        detail = await polite_get(client, job.url)
        if detail is None:
            return
        soup = BeautifulSoup(detail.text, "html.parser")
        body_el = (
            soup.select_one("div.field--name-body")
            or soup.select_one("div.job-description")
            or soup.select_one("main article")
        )
        if body_el and isinstance(body_el, Tag):
            text = " ".join(body_el.get_text(" ", strip=True).split())
            if len(text) > 50:
                job.description = text
