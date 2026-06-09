from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

import httpx

from localjobscout.db import Job, make_job_id
from localjobscout.scrapers.base import USER_AGENT, Scraper
from localjobscout.url_utils import normalise_adzuna_url

logger = logging.getLogger(__name__)

_API_BASE = "https://api.adzuna.com/v1/api/jobs/ca/search"
_RESULTS_PER_PAGE = 20


def _parse_entry(entry: object) -> Job | None:
    if not isinstance(entry, dict):
        return None

    title_val = entry.get("title")
    if not isinstance(title_val, str) or not title_val:
        return None

    raw_url = entry.get("redirect_url")
    if not isinstance(raw_url, str) or not raw_url:
        return None
    url_val = normalise_adzuna_url(raw_url)

    desc_val = entry.get("description", "")
    description = desc_val if isinstance(desc_val, str) else ""

    company_obj = entry.get("company", {})
    if isinstance(company_obj, dict):
        company_name = company_obj.get("display_name", "")
        company = company_name if isinstance(company_name, str) else ""
    else:
        company = ""

    location_obj = entry.get("location", {})
    if isinstance(location_obj, dict):
        loc_name = location_obj.get("display_name", "")
        loc_str = loc_name if isinstance(loc_name, str) else ""
    else:
        loc_str = ""

    created_val = entry.get("created")
    posted_at = created_val if isinstance(created_val, str) else None

    return Job(
        id=make_job_id("adzuna", url_val),
        source="adzuna",
        title=title_val,
        company=company,
        location=loc_str,
        url=url_val,
        description=description,
        posted_at=posted_at,
        first_seen=datetime.now(UTC).isoformat(),
        score=None,
        notified=False,
    )


class AdzunaScraper(Scraper):
    name = "adzuna"

    def __init__(
        self,
        *,
        app_id: str,
        app_key: str,
        query: str = "",
        max_pages: int = 5,
        location_override: str = "",
    ) -> None:
        self._app_id = app_id
        self._app_key = app_key
        self._query = query
        self._max_pages = max_pages
        self._location_override = location_override

    async def fetch(self, location: str) -> list[Job]:
        effective_location = self._location_override or location
        if not self._app_id:
            logger.warning("Adzuna app_id not set; skipping Adzuna scrape")
            return []

        jobs: list[Job] = []
        async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
            for page in range(1, self._max_pages + 1):
                if page > 1:
                    await asyncio.sleep(2)

                url = f"{_API_BASE}/{page}"
                params: dict[str, str | int] = {
                    "app_id": self._app_id,
                    "app_key": self._app_key,
                    "results_per_page": _RESULTS_PER_PAGE,
                    "what_or": self._query,
                    "where": effective_location,
                }

                try:
                    resp = await client.get(url, params=params, timeout=20.0)
                except httpx.HTTPError as exc:
                    logger.warning(
                        "HTTP error fetching Adzuna page %d: %s", page, exc
                    )
                    break

                if resp.status_code >= 400:
                    logger.warning(
                        "Bad status %d fetching Adzuna page %d",
                        resp.status_code,
                        page,
                    )
                    break

                try:
                    data = resp.json()
                except json.JSONDecodeError:
                    logger.warning(
                        "Adzuna API returned invalid JSON on page %d", page
                    )
                    break

                if not isinstance(data, dict):
                    break
                results = data.get("results")
                if not isinstance(results, list) or not results:
                    break

                for entry in results:
                    try:
                        job = _parse_entry(entry)
                    except Exception:
                        logger.exception("Failed to parse Adzuna entry, skipping")
                        continue
                    if job is not None:
                        jobs.append(job)

        return jobs
