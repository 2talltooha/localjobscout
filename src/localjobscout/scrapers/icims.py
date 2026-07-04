"""Generic iCIMS careers-portal scraper.

Many hospitals migrated their careers sites to iCIMS (e.g. Cambridge Memorial →
encareers-cmh.icims.com). The public portal is a JS app, but iCIMS also serves a
fully server-rendered job list at:

    https://{subdomain}.icims.com/jobs/search?ss=1&pr={page}&in_iframe=1

so plain httpx + BeautifulSoup works — no Playwright, no Cloudflare.

Row markup is stable:
- rows:        ``div.row`` containing an ``a.iCIMS_Anchor``
- title + url: the ``a.iCIMS_Anchor`` (href + ``<h3>`` text)
- snippet:     ``div.description``

Instantiate per hospital with its subdomain, display name, and city; the full
posting body is fetched from each job page (also ``in_iframe=1``).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag

from localjobscout.db import Job, make_job_id
from localjobscout.scrapers import fetcher
from localjobscout.scrapers.adaptive import all_matches, first
from localjobscout.scrapers.base import USER_AGENT, Scraper, polite_get

logger = logging.getLogger(__name__)

_MAX_LISTINGS = 80
_MAX_ENRICH = 40


# ── Adaptive (self-healing) extraction ───────────────────────────────────────
def extract_rows_adaptive(selector: Any, seen: set[str]) -> list[dict[str, str]]:
    """Pull job rows from an iCIMS search Selector via adaptive selectors."""
    anchors = all_matches(
        selector, "a.iCIMS_Anchor", identifier="icims_anchor_list"
    )
    out: list[dict[str, str]] = []
    for anchor in anchors:
        href = anchor.attrib.get("href")
        if not href or "/jobs/" not in href:
            continue
        if href in seen:
            continue
        seen.add(href)
        heading = first(anchor, "h3")
        title = (
            str(heading.text or "").strip()
            if heading is not None
            else str(anchor.text or "").strip()
        )
        if not title or len(title) < 3:
            continue
        snippet = ""
        row = anchor.find_ancestor(lambda e: e.has_class("row"))
        if row is not None:
            desc_el = first(row, "div.description")
            if desc_el is not None:
                snippet = " ".join(desc_el.get_all_text().split())
        out.append({"href": href, "title": title, "snippet": snippet})
    return out


def extract_description_adaptive(selector: Any) -> str:
    el = (
        first(selector, "div.iCIMS_JobContent", identifier="icims_job_content")
        or first(selector, "div.iCIMS_InfoMsg_Job")
        or first(selector, '[class*="JobDescription"]')
        or first(selector, "main")
    )
    if el is None:
        return ""
    return " ".join(el.get_all_text().split()).strip()


class ICIMSScraper(Scraper):
    """Server-rendered iCIMS portal scraper. Subclass or instantiate per site."""

    def __init__(
        self,
        *,
        name: str,
        subdomain: str,
        company: str,
        city: str,
        max_pages: int = 3,
        known_ids: frozenset[str] = frozenset(),
    ) -> None:
        self.name = name  # type: ignore[misc]  # per-instance source id
        self._subdomain = subdomain
        self._company = company
        self._city = city
        self._max_pages = max_pages
        self._known_ids = known_ids

    @property
    def _base(self) -> str:
        return f"https://{self._subdomain}.icims.com"

    async def fetch(self, location: str) -> list[Job]:
        if fetcher.adaptive_enabled():
            try:
                jobs = await self._fetch_adaptive(location)
                if jobs:
                    return jobs
                logger.debug("%s: adaptive yielded 0; using legacy path", self.name)
            except Exception:
                logger.exception("%s: adaptive path failed; using legacy", self.name)
        return await self._fetch_legacy(location)

    async def _fetch_adaptive(self, location: str) -> list[Job]:
        jobs: list[Job] = []
        seen: set[str] = set()
        for page in range(self._max_pages):
            if len(jobs) >= _MAX_LISTINGS:
                break
            url = f"{self._base}/jobs/search?ss=1&pr={page}&in_iframe=1"
            selector = await fetcher.fetch_selector(url, source=self.name)
            if selector is None:
                return jobs
            rows = extract_rows_adaptive(selector, seen)
            if not rows:
                break
            for row in rows[: _MAX_LISTINGS - len(jobs)]:
                jobs.append(
                    Job(
                        id=make_job_id(self.name, row["href"]),
                        source=self.name,
                        title=row["title"],
                        company=self._company,
                        location=f"{self._city}, ON",
                        url=row["href"],
                        description=row["snippet"]
                        or f"{row['title']} at {self._company}.",
                        posted_at=None,
                        first_seen=datetime.now(UTC).isoformat(),
                        score=None,
                        notified=False,
                    )
                )

        for job in jobs[:_MAX_ENRICH]:
            if job.id in self._known_ids:
                continue
            detail_sel = await fetcher.fetch_selector(job.url, source=self.name)
            if detail_sel is None:
                continue
            text = extract_description_adaptive(detail_sel)
            if text and len(text) > len(job.description):
                job.description = text[:8000]

        return jobs

    async def _fetch_legacy(self, location: str) -> list[Job]:
        jobs: list[Job] = []
        seen: set[str] = set()

        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            for page in range(self._max_pages):
                if len(jobs) >= _MAX_LISTINGS:
                    break
                url = (
                    f"{self._base}/jobs/search?ss=1&pr={page}&in_iframe=1"
                )
                resp = await polite_get(client, url)
                if resp is None:
                    break
                soup = BeautifulSoup(resp.text, "html.parser")
                page_jobs = self._parse_page(soup, seen)
                if not page_jobs:
                    break
                jobs.extend(page_jobs[: _MAX_LISTINGS - len(jobs)])

            for job in jobs[:_MAX_ENRICH]:
                await self._enrich(client, job)

        return jobs

    def _parse_page(self, soup: BeautifulSoup, seen: set[str]) -> list[Job]:
        results: list[Job] = []
        for anchor in soup.select("a.iCIMS_Anchor"):
            if not isinstance(anchor, Tag):
                continue
            href = anchor.get("href")
            if not isinstance(href, str) or "/jobs/" not in href:
                continue
            if href in seen:
                continue
            seen.add(href)
            heading = anchor.find("h3")
            title = (
                heading.get_text(strip=True)
                if isinstance(heading, Tag)
                else anchor.get_text(strip=True)
            )
            if not title or len(title) < 3:
                continue
            # description snippet sits in a sibling .description within the row
            snippet = ""
            row = anchor.find_parent("div", class_="row")
            if isinstance(row, Tag):
                desc_el = row.select_one("div.description")
                if desc_el:
                    snippet = desc_el.get_text(" ", strip=True)
            results.append(
                Job(
                    id=make_job_id(self.name, href),
                    source=self.name,
                    title=title,
                    company=self._company,
                    location=f"{self._city}, ON",
                    url=href,
                    description=snippet or f"{title} at {self._company}.",
                    posted_at=None,
                    first_seen=datetime.now(UTC).isoformat(),
                    score=None,
                    notified=False,
                )
            )
        if not results:
            logger.debug("%s: no iCIMS rows matched", self.name)
        return results

    async def _enrich(self, client: httpx.AsyncClient, job: Job) -> None:
        detail = await polite_get(client, job.url)
        if detail is None:
            return
        soup = BeautifulSoup(detail.text, "html.parser")
        body = (
            soup.select_one("div.iCIMS_JobContent")
            or soup.select_one("div.iCIMS_InfoMsg_Job")
            or soup.select_one('[class*="JobDescription"]')
            or soup.select_one("main")
        )
        if isinstance(body, Tag):
            text = " ".join(body.get_text(" ", strip=True).split())
            if len(text) > len(job.description):
                job.description = text[:8000]
