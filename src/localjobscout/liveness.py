"""Job-posting liveness verification.

Most rows in the DB accumulate across many scans; a large fraction get delisted
or stop accepting applications without ever saying so in their stored
description. Before surfacing a job to the user we re-fetch its URL and decide
whether it is still applyable.

Reliability varies by source:

- Adzuna / LinkedIn / RemoteOK / JobBank serve clean signals — a 404/410, a
  redirect to a "not found" page, or an explicit "no longer accepting
  applications" phrase. These we trust.
- Indeed sits behind Cloudflare and serves a ~700 KB React shell whose
  boilerplate contains misleading strings like "this job has expired" even on
  live postings. We cannot verify it, so we report UNKNOWN rather than guess.

`verify(url, source)` returns a `Liveness` with `.state` in
{"live", "dead", "unknown"} and a short human reason.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# Phrases that, on a verifiable source, mean the posting is closed/gone.
_DEAD_PHRASES: tuple[str, ...] = (
    "no longer accepting applications",
    "no longer accepting application",
    "this job is no longer available",
    "this job posting is no longer available",
    "job is no longer available",
    "this position has been filled",
    "this position is no longer available",
    "this job has expired",
    "this posting has expired",
    "posting has been closed",
    "this posting has been closed",
    "applications are no longer being accepted",
    "applications are now closed",
    "this competition is now closed",
    "the job you are looking for",          # ATS "not found" pages
    "job not found",
    "position you are looking for",
)

# Sources whose raw HTTP responses we trust to signal liveness.
_VERIFIABLE_SOURCES: frozenset[str] = frozenset(
    {"adzuna", "linkedin", "remoteok", "jobbank"}
)
# Sources we cannot check from a plain client (anti-bot / misleading markup).
_UNVERIFIABLE_SOURCES: frozenset[str] = frozenset({"indeed"})

_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


@dataclass(frozen=True)
class Liveness:
    state: str  # "live" | "dead" | "unknown"
    reason: str
    full_text: str | None = None  # extracted page text when fetched live

    @property
    def applyable(self) -> bool:
        """True only when positively verified live. UNKNOWN is not applyable
        for the purpose of a 'show me only live jobs' filter."""
        return self.state == "live"


# Cap stored description length — enough for filters, avoids bloating the DB.
_MAX_TEXT = 8000


def extract_main_text(html: str) -> str:
    """Best-effort plain-text extraction from an arbitrary job/ATS page.

    Strips scripts/nav/chrome and collapses whitespace. Not perfect per-site,
    but turns a truncated Adzuna snippet into the full posting body so the
    relevance/credential filters can judge on real content."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(
        ["script", "style", "noscript", "nav", "header", "footer", "svg", "form"]
    ):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    return str(text[:_MAX_TEXT])


def verify(url: str, source: str, *, timeout: float = 12.0) -> Liveness:
    """Re-fetch a posting and classify it as live / dead / unknown."""
    if not url:
        return Liveness("unknown", "no url")
    if source in _UNVERIFIABLE_SOURCES:
        return Liveness("unknown", f"{source} blocks verification")

    try:
        with httpx.Client(
            follow_redirects=True, timeout=timeout, headers=_UA
        ) as client:
            resp = client.get(url)
    except httpx.HTTPError as exc:
        return Liveness("unknown", f"fetch error: {type(exc).__name__}")

    if resp.status_code in (404, 410):
        return Liveness("dead", f"HTTP {resp.status_code}")
    if resp.status_code == 403:
        # Blocked — cannot read the page to judge.
        return Liveness("unknown", "HTTP 403 (blocked)")
    if resp.status_code >= 400:
        return Liveness("unknown", f"HTTP {resp.status_code}")

    body = resp.text.lower()
    for phrase in _DEAD_PHRASES:
        if phrase in body:
            return Liveness("dead", f"dead phrase: {phrase!r}")

    text: str | None = None
    try:
        text = extract_main_text(resp.text)
    except Exception as exc:  # extraction must never break verification
        logger.debug("text extraction failed for %s: %s", url, exc)
    return Liveness("live", f"HTTP {resp.status_code}", full_text=text)
