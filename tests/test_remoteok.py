from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
import respx

from localjobscout.db import make_job_id
from localjobscout.scrapers.remoteok import RemoteOKScraper

_HOST = "remoteok.com"
_ROBOTS_URL = f"https://{_HOST}/robots.txt"
_API_URL = f"https://{_HOST}/api"
_ROBOTS_ALLOW = "User-agent: *\nAllow: /"

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "remoteok_api.json"
_FIXTURE_DATA: list[object] = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
_FIXTURE_TEXT = json.dumps(_FIXTURE_DATA)

_KEPT_URLS = {
    "https://remoteok.com/jobs/1001",  # Canada
    "https://remoteok.com/jobs/1002",  # Worldwide
    "https://remoteok.com/jobs/1006",  # no location
    "https://remoteok.com/jobs/1007",  # Anywhere
}


@pytest.mark.asyncio
async def test_happy_path_filtering() -> None:
    """Exactly 4 jobs returned: Canada, Worldwide, no-location, Anywhere."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_ALLOW)
        mock.get(_API_URL).respond(200, text=_FIXTURE_TEXT)

        jobs = await RemoteOKScraper().fetch("Waterloo, ON")

    assert len(jobs) == 4
    assert all(j.source == "remoteok" for j in jobs)
    assert {j.url for j in jobs} == _KEPT_URLS
    expected_ids = {make_job_id("remoteok", url) for url in _KEPT_URLS}
    assert {j.id for j in jobs} == expected_ids


@pytest.mark.asyncio
async def test_us_only_entry_filtered() -> None:
    """Index-3 entry (United States only) must not appear in results."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_ALLOW)
        mock.get(_API_URL).respond(200, text=_FIXTURE_TEXT)

        jobs = await RemoteOKScraper().fetch("Waterloo, ON")

    assert not any(j.url == "https://remoteok.com/jobs/1003" for j in jobs)


@pytest.mark.asyncio
async def test_malformed_entries_skipped() -> None:
    """Entries missing position (index 4) or url (index 5) are silently dropped."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_ALLOW)
        mock.get(_API_URL).respond(200, text=_FIXTURE_TEXT)

        jobs = await RemoteOKScraper().fetch("Waterloo, ON")

    urls = {j.url for j in jobs}
    assert "https://remoteok.com/jobs/1004" not in urls
    assert "https://remoteok.com/jobs/1005" not in urls


@pytest.mark.asyncio
async def test_html_description_stripped() -> None:
    """Index-7 job description has HTML tags removed."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_ALLOW)
        mock.get(_API_URL).respond(200, text=_FIXTURE_TEXT)

        jobs = await RemoteOKScraper().fetch("Waterloo, ON")

    job7 = next(j for j in jobs if j.url == "https://remoteok.com/jobs/1007")
    assert "<" not in job7.description
    assert ">" not in job7.description
    assert job7.description  # text content still present


@pytest.mark.asyncio
async def test_empty_api_response() -> None:
    """Metadata-only response (no jobs) returns []."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_ALLOW)
        mock.get(_API_URL).respond(200, text='[{"legal": "terms"}]')

        jobs = await RemoteOKScraper().fetch("Waterloo, ON")

    assert jobs == []


@pytest.mark.asyncio
async def test_api_500_returns_empty() -> None:
    """500 from API → polite_get returns None → fetch returns []."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_ALLOW)
        mock.get(_API_URL).respond(500)

        jobs = await RemoteOKScraper().fetch("Waterloo, ON")

    assert jobs == []


@pytest.mark.asyncio
async def test_invalid_json_returns_empty(caplog: pytest.LogCaptureFixture) -> None:
    """Non-JSON body → warning logged, [] returned."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_ALLOW)
        mock.get(_API_URL).respond(200, text="not json at all")

        with caplog.at_level(logging.WARNING):
            jobs = await RemoteOKScraper().fetch("Waterloo, ON")

    assert jobs == []
    assert "invalid JSON" in caplog.text
