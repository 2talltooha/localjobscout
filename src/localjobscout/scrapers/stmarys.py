"""St. Mary's General Hospital careers scraper (Kitchener, ON).

St. Mary's is a Catholic hospital serving the Waterloo Region. Their
careers page lists current openings. If the portal structure changes,
update _LISTING_URL and CSS selectors below.
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
from localjobscout.scrapers.adaptive import first, first_nonempty
from localjobscout.scrapers.base import USER_AGENT, Scraper, polite_get

logger = logging.getLogger(__name__)

_LISTING_URL = "https://www.smgh.ca/about-st-marys/careers"
_MAX_LISTINGS = 100

_ITEM_CANDIDATES = [
    "li.views-row",
    "div.view-content > div",
    "article.node--type-job",
    "table.career-table tbody tr",
    ".job-posting",
]
_BODY_CANDIDATES = [
    "div.field--name-body",
    "div.job-description",
    "main article",
]


# ── Adaptive (self-healing) extraction ───────────────────────────────────────
def extract_items_adaptive(
    selector: Any, base_url: str, seen_urls: set[str]
) -> list[dict[str, str]]:
    """Pull job items from a stmarys listing Selector via adaptive selectors."""
    items = first_nonempty(selector, _ITEM_CANDIDATES, identifier="stmarys_item_list")
    out: list[dict[str, str]] = []
    for item in items:
        link = first(item, "a")
        if link is None:
            continue
        href = link.attrib.get("href")
        if not href:
            continue
        detail_url = urljoin(base_url, href)
        if detail_url in seen_urls:
            continue
        seen_urls.add(detail_url)
        title = str(link.text or "").strip()
        if not title or len(title) < 4:
            continue
        dept_el = first(item, ".department, .field--name-field-department")
        department = str(dept_el.text or "").strip() if dept_el is not None else ""
        out.append({"href": detail_url, "title": title, "department": department})
    return out


def extract_description_adaptive(selector: Any) -> str:
    el = first_nonempty(selector, _BODY_CANDIDATES, identifier="stmarys_job_desc")
    if not el:
        return ""
    text = " ".join(el[0].get_all_text().split())
    return text if len(text) > 50 else ""


class StMarysScraper(Scraper):
    name = "stmarys"

    def __init__(self, max_pages: int = 2) -> None:
        self._max_pages = max_pages

    async def fetch(self, location: str) -> list[Job]:
        if fetcher.adaptive_enabled():
            try:
                jobs = await self._fetch_adaptive(location)
                if jobs:
                    return jobs
                logger.debug("stmarys: adaptive yielded 0; using legacy path")
            except Exception:
                logger.exception("stmarys: adaptive path failed; using legacy")
        return await self._fetch_legacy(location)

    async def _fetch_adaptive(self, location: str) -> list[Job]:
        jobs: list[Job] = []
        seen_urls: set[str] = set()
        for page in range(self._max_pages):
            if len(jobs) >= _MAX_LISTINGS:
                break
            url = _LISTING_URL if page == 0 else f"{_LISTING_URL}?page={page}"
            selector = await fetcher.fetch_selector(url, source="stmarys")
            if selector is None:
                return jobs
            items = extract_items_adaptive(selector, url, seen_urls)
            if not items:
                break
            for item in items:
                if len(jobs) >= _MAX_LISTINGS:
                    break
                description = "Location: Kitchener, ON"
                if item["department"]:
                    description = f"Department: {item['department']}\n{description}"
                detail_sel = await fetcher.fetch_selector(
                    item["href"], source="stmarys"
                )
                if detail_sel is not None:
                    body = extract_description_adaptive(detail_sel)
                    if body:
                        description = body
                jobs.append(
                    Job(
                        id=make_job_id("stmarys", item["href"]),
                        source="stmarys",
                        title=item["title"],
                        company="St. Mary's General Hospital",
                        location="Kitchener, ON",
                        url=item["href"],
                        description=description,
                        posted_at=None,
                        first_seen=datetime.now(UTC).isoformat(),
                        score=None,
                        notified=False,
                    )
                )
        return jobs

    async def _fetch_legacy(self, location: str) -> list[Job]:
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
