from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import quote_plus, urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from localjobscout.db import Job, make_job_id
from localjobscout.scrapers.base import USER_AGENT, Scraper, polite_get
from localjobscout.url_utils import normalise_jobbank_url

logger = logging.getLogger(__name__)

_SEARCH_BASE = "https://www.jobbank.gc.ca/jobsearch/jobsearch"
_MAX_LISTINGS = 100


class JobBankScraper(Scraper):
    name = "jobbank"

    def __init__(self, max_pages: int = 3, query: str = "") -> None:
        self._max_pages = max_pages
        self._query = query

    async def fetch(self, location: str) -> list[Job]:
        jobs: list[Job] = []

        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}
        ) as client:
            for page in range(1, self._max_pages + 1):
                if len(jobs) >= _MAX_LISTINGS:
                    break

                url = (
                    f"{_SEARCH_BASE}"
                    f"?searchstring={quote_plus(self._query)}"
                    f"&locationstring={location}"
                )
                if page > 1:
                    url += f"&page={page}"

                resp = await polite_get(client, url)
                if resp is None:
                    break

                soup = BeautifulSoup(resp.text, "html.parser")
                cards = soup.select("article a.resultJobItem")
                if not cards:
                    break

                for card in cards:
                    if len(jobs) >= _MAX_LISTINGS:
                        break
                    job = await self._parse_card(client, card, url)
                    if job is not None:
                        jobs.append(job)

        return jobs

    async def _parse_card(
        self,
        client: httpx.AsyncClient,
        card: Tag,
        page_url: str,
    ) -> Job | None:
        try:
            href = card.get("href")
            if not href or not isinstance(href, str):
                return None
            detail_url = normalise_jobbank_url(urljoin(page_url, href))

            title_tag = card.select_one(".noctitle")
            if title_tag is None:
                return None
            title = title_tag.get_text(strip=True)
            if not title:
                return None

            employer_tag = card.select_one(".business")
            company = employer_tag.get_text(strip=True) if employer_tag else ""

            location_tag = card.select_one(".location")
            location = location_tag.get_text(strip=True) if location_tag else ""

            description, posted_at = await self._fetch_detail(client, detail_url)

            return Job(
                id=make_job_id("jobbank", detail_url),
                source="jobbank",
                title=title,
                company=company,
                location=location,
                url=detail_url,
                description=description,
                posted_at=posted_at,
                first_seen=datetime.now(UTC).isoformat(),
                score=None,
                notified=False,
            )
        except Exception:
            logger.exception("Failed to parse card, skipping")
            return None

    async def _fetch_detail(
        self,
        client: httpx.AsyncClient,
        url: str,
    ) -> tuple[str, str | None]:
        resp = await polite_get(client, url)
        if resp is None:
            return "", None

        soup = BeautifulSoup(resp.text, "html.parser")

        desc_tag = (
            soup.select_one("#jobDescriptionId")
            or soup.select_one(".job-posting-detail-requirements")
        )
        if desc_tag is not None:
            description = desc_tag.get_text(separator=" ", strip=True)
        else:
            main_tag = soup.select_one("main")
            if main_tag is not None:
                paragraphs = main_tag.find_all("p")
                description = " ".join(p.get_text(strip=True) for p in paragraphs)
            else:
                description = ""

        time_tag = soup.select_one("time[datetime]")
        posted_at: str | None = None
        if time_tag is not None:
            dt_val = time_tag.get("datetime")
            if isinstance(dt_val, str):
                posted_at = dt_val

        return description.strip(), posted_at
