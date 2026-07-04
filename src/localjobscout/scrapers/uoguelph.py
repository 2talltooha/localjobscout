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

_SEARCH_BASE = "https://careers.uoguelph.ca/search/"
_RESULTS_PER_PAGE = 20
_MAX_LISTINGS = 100


# ── Adaptive (self-healing) extraction ───────────────────────────────────────
def extract_tiles_adaptive(selector: Any) -> list[dict[str, str]]:
    """Pull job tiles from a uoguelph search Selector via adaptive selectors."""
    tiles = all_matches(selector, "li.job-tile", identifier="uoguelph_tile_list")
    out: list[dict[str, str]] = []
    for tile in tiles:
        link = first(tile, "a.jobTitle-link")
        if link is None:
            continue
        href = link.attrib.get("href")
        title = str(link.text or "").strip()
        if not href or not title:
            continue
        out.append(
            {
                "href": href,
                "title": title,
                "location": _field_value_adaptive(tile, "location"),
                "division": _field_value_adaptive(tile, "facility"),
                "department": _field_value_adaptive(tile, "dept"),
            }
        )
    return out


def _field_value_adaptive(tile: Any, field_class: str) -> str:
    divs = all_matches(tile, f".section-field.{field_class} div")
    if not divs:
        return ""
    return str(divs[-1].text or "").strip()


def extract_description_adaptive(selector: Any) -> str:
    return taleo_description(selector, "uoguelph_job_desc")


class UofGScraper(Scraper):
    name = "uoguelph"

    def __init__(
        self, max_pages: int = 3, known_ids: frozenset[str] = frozenset()
    ) -> None:
        self._max_pages = max_pages
        # Job ids already in the DB — the adaptive path skips the detail-page
        # fetch for these instead of re-downloading an already-stored
        # description every hourly scan.
        self._known_ids = known_ids

    async def fetch(self, location: str) -> list[Job]:
        if fetcher.adaptive_enabled():
            try:
                jobs = await self._fetch_adaptive(location)
                if jobs:
                    return jobs
                logger.debug("uoguelph: adaptive yielded 0; using legacy path")
            except Exception:
                logger.exception("uoguelph: adaptive path failed; using legacy")
        return await self._fetch_legacy(location)

    async def _fetch_adaptive(self, location: str) -> list[Job]:
        jobs: list[Job] = []
        seen_urls: set[str] = set()
        for page in range(self._max_pages):
            if len(jobs) >= _MAX_LISTINGS:
                break
            startrow = page * _RESULTS_PER_PAGE
            url = f"{_SEARCH_BASE}?startrow={startrow}"
            selector = await fetcher.fetch_selector(url, source="uoguelph")
            if selector is None:
                return jobs
            tiles = extract_tiles_adaptive(selector)
            if not tiles:
                break
            page_added = 0
            for tile in tiles:
                if len(jobs) >= _MAX_LISTINGS:
                    break
                detail_url = urljoin(url, tile["href"])
                if detail_url in seen_urls:
                    continue
                seen_urls.add(detail_url)
                job_id = make_job_id("uoguelph", detail_url)
                description = self._build_description(
                    tile["division"], tile["department"], tile["location"]
                )
                if job_id not in self._known_ids:
                    detail_sel = await fetcher.fetch_selector(
                        detail_url, source="uoguelph"
                    )
                    if detail_sel is not None:
                        body = extract_description_adaptive(detail_sel)
                        if body:
                            description = body
                jobs.append(
                    Job(
                        id=job_id,
                        source="uoguelph",
                        title=tile["title"],
                        company="University of Guelph",
                        location=tile["location"],
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

    @staticmethod
    def _build_description(division: str, department: str, location: str) -> str:
        parts: list[str] = []
        if division:
            parts.append(f"Division: {division}")
        if department:
            parts.append(f"Department: {department}")
        if location:
            parts.append(f"Location: {location}")
        return "\n".join(parts)

    async def _fetch_legacy(self, location: str) -> list[Job]:
        jobs: list[Job] = []
        seen_urls: set[str] = set()

        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}
        ) as client:
            for page in range(self._max_pages):
                if len(jobs) >= _MAX_LISTINGS:
                    break

                startrow = page * _RESULTS_PER_PAGE
                url = f"{_SEARCH_BASE}?startrow={startrow}"

                resp = await polite_get(client, url)
                if resp is None:
                    break

                soup = BeautifulSoup(resp.text, "html.parser")
                tiles = soup.select("li.job-tile")
                if not tiles:
                    break

                page_added = 0
                for tile in tiles:
                    if len(jobs) >= _MAX_LISTINGS:
                        break
                    job = self._parse_tile(tile, url)
                    if job is None:
                        continue
                    if job.url in seen_urls:
                        continue
                    seen_urls.add(job.url)
                    await self._enrich_description(client, job)
                    jobs.append(job)
                    page_added += 1

                if page_added == 0:
                    break

        return jobs

    def _parse_tile(self, tile: Tag, page_url: str) -> Job | None:
        try:
            link = tile.select_one("a.jobTitle-link")
            if link is None:
                return None
            href = link.get("href")
            if not isinstance(href, str) or not href:
                return None
            title = link.get_text(strip=True)
            if not title:
                return None
            detail_url = urljoin(page_url, href)

            location = self._field_value(tile, "location")
            division = self._field_value(tile, "facility")
            department = self._field_value(tile, "dept")

            description_parts: list[str] = []
            if division:
                description_parts.append(f"Division: {division}")
            if department:
                description_parts.append(f"Department: {department}")
            if location:
                description_parts.append(f"Location: {location}")
            description = "\n".join(description_parts)

            return Job(
                id=make_job_id("uoguelph", detail_url),
                source="uoguelph",
                title=title,
                company="University of Guelph",
                location=location,
                url=detail_url,
                description=description,
                posted_at=None,
                first_seen=datetime.now(UTC).isoformat(),
                score=None,
                notified=False,
            )
        except Exception:
            logger.exception("Failed to parse uoguelph tile, skipping")
            return None

    @staticmethod
    def _description_from_detail_html(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        desc_el = soup.select_one(
            'span[itemprop="description"] span.jobdescription'
        ) or soup.select_one("span.jobdescription")
        if desc_el is None or not isinstance(desc_el, Tag):
            return ""
        text = desc_el.get_text("\n", strip=True)
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

    @staticmethod
    def _field_value(tile: Tag, field_class: str) -> str:
        # The SuccessFactors markup renders desktop, tablet, and mobile copies
        # of each tile. We only need the first matching section-field for the
        # field; the label <span> sits next to a <div> holding the value.
        field = tile.select_one(f".section-field.{field_class}")
        if field is None:
            return ""
        # The value lives in the last <div> child of the section-field
        # (the first child is the label span).
        value_divs = field.find_all("div")
        if not value_divs:
            return ""
        last_div = value_divs[-1]
        if not isinstance(last_div, Tag):
            return ""
        return last_div.get_text(strip=True)
