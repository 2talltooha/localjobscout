from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import httpx
from bs4 import BeautifulSoup

from localjobscout.db import Job, make_job_id
from localjobscout.scrapers.base import USER_AGENT, Scraper, polite_get

logger = logging.getLogger(__name__)

_API_URL = "https://remoteok.com/api"
_MAX_JOBS = 100
_LOCATION_ALLOW = ("canada", "worldwide", "anywhere")


def _location_eligible(location: str | None) -> bool:
    if not location:
        return True
    return any(kw in location.lower() for kw in _LOCATION_ALLOW)


def _parse_entry(entry: object) -> Job | None:
    if not isinstance(entry, dict):
        return None

    job_id = entry.get("id")
    position = entry.get("position")
    url = entry.get("url")

    if not job_id or not position or not url:
        return None
    if not isinstance(position, str) or not isinstance(url, str):
        return None

    loc_val = entry.get("location")
    loc_str: str | None = loc_val if isinstance(loc_val, str) else None
    if not _location_eligible(loc_str):
        return None

    desc_raw = entry.get("description", "")
    description = (
        BeautifulSoup(desc_raw, "html.parser").get_text(separator=" ", strip=True)
        if isinstance(desc_raw, str) and desc_raw
        else ""
    )

    company_val = entry.get("company", "")
    company = company_val if isinstance(company_val, str) else ""

    date_val = entry.get("date")
    posted_at = date_val if isinstance(date_val, str) else None

    return Job(
        id=make_job_id("remoteok", url),
        source="remoteok",
        title=position,
        company=company,
        location=loc_str or "",
        url=url,
        description=description,
        posted_at=posted_at,
        first_seen=datetime.now(UTC).isoformat(),
        score=None,
        notified=False,
    )


class RemoteOKScraper(Scraper):
    name = "remoteok"

    async def fetch(self, location: str) -> list[Job]:
        async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
            # source tag pins this JSON API to the legacy httpx path
            # (fetch.legacy_sources) — Scrapling wraps raw JSON in HTML.
            resp = await polite_get(client, _API_URL, source="remoteok")

        if resp is None:
            return []

        try:
            data = resp.json()
        except json.JSONDecodeError:
            logger.warning("RemoteOK API returned invalid JSON")
            return []

        if not isinstance(data, list) or not data:
            return []

        jobs: list[Job] = []
        for entry in data[1:]:  # index 0 is API metadata, skip it
            if len(jobs) >= _MAX_JOBS:
                break
            try:
                job = _parse_entry(entry)
            except Exception:
                logger.exception("Failed to parse RemoteOK entry, skipping")
                continue
            if job is not None:
                jobs.append(job)
        return jobs
