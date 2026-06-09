"""URL normalisation for scrapers.

Strips session IDs and tracking query params so the same job posting produces
a stable URL (and therefore a stable sha256 id) across scrape runs.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

# JobBank embeds jsessionid as a path matrix param:
# /jobposting/1234;jsessionid=ABC.jobsearchN
_JOBBANK_JSESSIONID_RE = re.compile(r";jsessionid=[^?#&/]*", re.IGNORECASE)

# Query params that are per-request noise (not part of the resource identity)
_JOBBANK_STRIP_PARAMS = frozenset({"source"})
_ADZUNA_STRIP_PARAMS = frozenset({"se", "v", "utm_medium", "utm_source",
                                   "utm_campaign", "utm_term", "utm_content"})


def _strip_query_params(url: str, strip: frozenset[str]) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    cleaned = {k: v for k, v in qs.items() if k.lower() not in strip}
    new_query = urlencode(cleaned, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def normalise_jobbank_url(url: str) -> str:
    """Remove jsessionid path param and source tracking query param."""
    url = _JOBBANK_JSESSIONID_RE.sub("", url)
    return _strip_query_params(url, _JOBBANK_STRIP_PARAMS)


def normalise_adzuna_url(url: str) -> str:
    """Remove Adzuna per-click tracking params (se=, v=, utm_*)."""
    return _strip_query_params(url, _ADZUNA_STRIP_PARAMS)
