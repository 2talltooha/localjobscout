from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from localjobscout.db import Job, make_job_id
from localjobscout.scrapers.base import USER_AGENT, Scraper, polite_get

logger = logging.getLogger(__name__)

_LISTING_URL = "https://employment.conestogac.on.ca/"
_MAX_LISTINGS = 100


class ConestogaScraper(Scraper):
    name = "conestoga"

    def __init__(self, max_pages: int = 1) -> None:
        # Conestoga renders all openings on a single page; max_pages is
        # accepted for config-shape uniformity but only the first page is
        # ever fetched.
        self._max_pages = max_pages

    async def fetch(self, location: str) -> list[Job]:
        jobs: list[Job] = []

        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}
        ) as client:
            resp = await polite_get(client, _LISTING_URL)
            if resp is None:
                return jobs

            soup = BeautifulSoup(resp.text, "html.parser")
            tables = soup.select("table.table.table-striped")
            for table in tables:
                for row in table.select("tr"):
                    if len(jobs) >= _MAX_LISTINGS:
                        break
                    # Header rows are class="tableheader" and contain <th>s.
                    if "tableheader" in (row.get("class") or []):
                        continue
                    if not row.find("td"):
                        continue
                    job = self._parse_row(row)
                    if job is not None:
                        jobs.append(job)
                if len(jobs) >= _MAX_LISTINGS:
                    break

            for job in jobs:
                await self._enrich_description(client, job)

        return jobs

    @staticmethod
    def _description_from_detail_html(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        sections: list[str] = []
        for h2 in soup.find_all("h2"):
            parent = h2.find_parent("div", class_="col-12")
            if parent is None:
                continue
            text = " ".join(parent.get_text(" ", strip=True).split())
            if len(text) > 30:
                sections.append(text)
        return "\n\n".join(sections)

    async def _enrich_description(
        self, client: httpx.AsyncClient, job: Job
    ) -> None:
        detail = await polite_get(client, job.url)
        if detail is None:
            return
        body = self._description_from_detail_html(detail.text)
        if body:
            # Preserve original metadata header (requisition/location/closing)
            # so prefilter year-experience scans still see structured fields.
            if job.description:
                job.description = f"{job.description}\n\n{body}"
            else:
                job.description = body

    def _parse_row(self, row: Tag) -> Job | None:
        try:
            cells = row.find_all("td")
            if len(cells) < 4:
                return None
            req_cell, title_cell, location_cell, closing_cell = cells[:4]
            if not (
                isinstance(req_cell, Tag)
                and isinstance(title_cell, Tag)
                and isinstance(location_cell, Tag)
                and isinstance(closing_cell, Tag)
            ):
                return None

            link = req_cell.find("a")
            if not isinstance(link, Tag):
                return None
            href = link.get("href")
            if not isinstance(href, str) or not href:
                return None
            requisition = link.get_text(strip=True)
            detail_url = urljoin(_LISTING_URL, href)

            title = title_cell.get_text(strip=True)
            if not title:
                return None

            location = location_cell.get_text(strip=True)
            closing = closing_cell.get_text(strip=True)

            description_parts: list[str] = []
            if requisition:
                description_parts.append(f"Requisition: {requisition}")
            if location:
                description_parts.append(f"Location: {location}")
            if closing:
                description_parts.append(f"Closing: {closing}")
            description = "\n".join(description_parts)

            return Job(
                id=make_job_id("conestoga", detail_url),
                source="conestoga",
                title=title,
                company="Conestoga College",
                location=location,
                url=detail_url,
                description=description,
                posted_at=None,
                first_seen=datetime.now(UTC).isoformat(),
                score=None,
                notified=False,
            )
        except Exception:
            logger.exception("Failed to parse conestoga row, skipping")
            return None
