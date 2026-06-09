from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from localjobscout.db import Job, make_job_id
from localjobscout.scrapers.base import Scraper

logger = logging.getLogger(__name__)

_CXS_URL = (
    "https://uwaterloo.wd3.myworkdayjobs.com/wday/cxs/uwaterloo/uw_careers/jobs"
)
_BASE_JOB_URL = "https://uwaterloo.wd3.myworkdayjobs.com/uw_careers"
_RESULTS_PER_PAGE = 20
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_COMPANY = "University of Waterloo"


def _job_from_posting(posting: dict[str, Any], url: str) -> Job | None:
    title = posting.get("title")
    if not isinstance(title, str) or not title.strip():
        return None

    loc_raw = posting.get("locationsText", "")
    location = loc_raw if isinstance(loc_raw, str) else ""

    posted_raw = posting.get("postedOn")
    posted_at = posted_raw if isinstance(posted_raw, str) else None

    parts = [title.strip(), location.strip()]
    description = " — ".join(p for p in parts if p)

    return Job(
        id=make_job_id("uwaterloo", url),
        source="uwaterloo",
        title=title.strip(),
        company=_COMPANY,
        location=location.strip(),
        url=url,
        description=description,
        posted_at=posted_at,
        first_seen=datetime.now(UTC).isoformat(),
        score=None,
        notified=False,
    )


class UWaterlooScraper(Scraper):
    """University of Waterloo careers via Workday CXS JSON API (no auth)."""

    name = "uwaterloo"

    def __init__(
        self,
        max_pages: int = 3,
        *,
        query: str = "research assistant",
    ) -> None:
        self._max_pages = max_pages
        self._query = query or "research assistant"

    async def fetch(self, location: str) -> list[Job]:
        del location  # CXS uses searchText only; geographic facets not used here
        jobs: list[Job] = []
        headers = {
            "Content-Type": "application/json",
            "User-Agent": _CHROME_UA,
            "Accept": "application/json,*/*",
        }

        async with httpx.AsyncClient(headers=headers) as client:
            for page_idx in range(self._max_pages):
                if page_idx > 0:
                    await asyncio.sleep(2)

                offset = page_idx * _RESULTS_PER_PAGE
                payload = {
                    "appliedFacets": {},
                    "limit": _RESULTS_PER_PAGE,
                    "offset": offset,
                    "searchText": self._query,
                }
                req_headers = {
                    **headers,
                    "X-Workday-Client-Request-ID": str(uuid.uuid4()),
                }

                try:
                    resp = await client.post(
                        _CXS_URL,
                        json=payload,
                        headers=req_headers,
                        timeout=30.0,
                    )
                except httpx.HTTPError as exc:
                    logger.warning("HTTP error posting to UWaterloo CXS: %s", exc)
                    break

                if resp.status_code >= 400:
                    logger.warning(
                        "Bad status %s from UWaterloo CXS", resp.status_code
                    )
                    break

                try:
                    data = resp.json()
                except ValueError as exc:
                    logger.warning("Invalid JSON from UWaterloo CXS: %s", exc)
                    break

                postings = data.get("jobPostings")
                if not isinstance(postings, list):
                    logger.warning(
                        "UWaterloo CXS response missing jobPostings list; "
                        "stop pagination with %d jobs collected",
                        len(jobs),
                    )
                    break

                total_raw = data.get("total")
                total = total_raw if isinstance(total_raw, int) else None

                for posting in postings:
                    if not isinstance(posting, dict):
                        continue
                    ext = posting.get("externalPath")
                    if not isinstance(ext, str) or not ext.startswith("/"):
                        continue
                    url = f"{_BASE_JOB_URL}{ext}"
                    job = _job_from_posting(posting, url)
                    if job is not None:
                        jobs.append(job)

                if not postings:
                    break
                if total is not None and offset + len(postings) >= total:
                    break

        return jobs
