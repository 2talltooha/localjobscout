from __future__ import annotations

import json
import logging

import pytest
import respx

from localjobscout.db import make_job_id
from localjobscout.scrapers.adzuna import AdzunaScraper

_API_BASE = "https://api.adzuna.com/v1/api/jobs/ca/search"
_FAKE_APP_ID = "test_id"
_FAKE_APP_KEY = "test_key"


def _build_response(
    results: list[dict[str, object]],
    count: int = 100,
) -> str:
    return json.dumps({"results": results, "count": count})


@pytest.mark.asyncio
async def test_happy_path_returns_jobs_with_correct_fields() -> None:
    entries: list[dict[str, object]] = [
        {
            "title": "Research Assistant — Biology Lab",
            "description": (
                "Looking for a motivated student to assist with bench work."
            ),
            "redirect_url": "https://www.adzuna.ca/details/12345",
            "created": "2026-05-10T14:23:00Z",
            "company": {"display_name": "University of Toronto"},
            "location": {"display_name": "Toronto, ON"},
            "id": "12345",
        },
        {
            "title": "Clinical Research Coordinator",
            "description": "Coordinate clinical trials and patient outreach.",
            "redirect_url": "https://www.adzuna.ca/details/67890",
            "created": "2026-05-09T10:00:00Z",
            "company": {"display_name": "Sunnybrook"},
            "location": {"display_name": "Toronto, ON"},
            "id": "67890",
        },
    ]
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{_API_BASE}/1").respond(200, text=_build_response(entries))

        scraper = AdzunaScraper(
            app_id=_FAKE_APP_ID,
            app_key=_FAKE_APP_KEY,
            query="biology",
            max_pages=1,
        )
        jobs = await scraper.fetch("Waterloo, ON")

    assert len(jobs) == 2
    assert all(j.source == "adzuna" for j in jobs)
    assert all(j.score is None for j in jobs)
    assert all(j.notified is False for j in jobs)

    j1 = jobs[0]
    assert j1.title == "Research Assistant — Biology Lab"
    assert j1.url == "https://www.adzuna.ca/details/12345"
    assert j1.id == make_job_id("adzuna", "https://www.adzuna.ca/details/12345")
    assert j1.company == "University of Toronto"
    assert j1.location == "Toronto, ON"
    assert j1.posted_at == "2026-05-10T14:23:00Z"

    j2 = jobs[1]
    assert j2.title == "Clinical Research Coordinator"
    assert j2.url == "https://www.adzuna.ca/details/67890"
    assert j2.id == make_job_id("adzuna", "https://www.adzuna.ca/details/67890")
    assert j2.company == "Sunnybrook"


@pytest.mark.asyncio
async def test_pagination_stops_at_max_pages() -> None:
    page1: list[dict[str, object]] = [
        {
            "title": "Page 1 Job",
            "description": "First-page result.",
            "redirect_url": "https://www.adzuna.ca/details/p1",
            "created": "2026-05-10T14:23:00Z",
            "company": {"display_name": "Acme"},
            "location": {"display_name": "Toronto, ON"},
            "id": "p1",
        },
    ]
    page2: list[dict[str, object]] = [
        {
            "title": "Page 2 Job",
            "description": "Second-page result.",
            "redirect_url": "https://www.adzuna.ca/details/p2",
            "created": "2026-05-09T10:00:00Z",
            "company": {"display_name": "Acme"},
            "location": {"display_name": "Toronto, ON"},
            "id": "p2",
        },
    ]

    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{_API_BASE}/1").respond(200, text=_build_response(page1))
        mock.get(f"{_API_BASE}/2").respond(200, text=_build_response(page2))

        scraper = AdzunaScraper(
            app_id=_FAKE_APP_ID,
            app_key=_FAKE_APP_KEY,
            query="biology",
            max_pages=1,
        )
        jobs = await scraper.fetch("Waterloo, ON")

        assert len(mock.calls) == 1

    assert len(jobs) == 1
    assert jobs[0].url == "https://www.adzuna.ca/details/p1"


@pytest.mark.asyncio
async def test_missing_company_field_handled_gracefully() -> None:
    entries: list[dict[str, object]] = [
        {
            "title": "No Company Key Job",
            "description": "Description text.",
            "redirect_url": "https://www.adzuna.ca/details/no_company",
            "created": "2026-05-10T14:23:00Z",
            "location": {"display_name": "Toronto, ON"},
            "id": "1",
        },
        {
            "title": "Empty Company Dict Job",
            "description": "Description text.",
            "redirect_url": "https://www.adzuna.ca/details/empty_company",
            "created": "2026-05-10T14:23:00Z",
            "company": {},
            "location": {"display_name": "Toronto, ON"},
            "id": "2",
        },
    ]

    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{_API_BASE}/1").respond(200, text=_build_response(entries))

        scraper = AdzunaScraper(
            app_id=_FAKE_APP_ID,
            app_key=_FAKE_APP_KEY,
            query="biology",
            max_pages=1,
        )
        jobs = await scraper.fetch("Waterloo, ON")

    assert len(jobs) == 2
    assert all(j.company == "" for j in jobs)


@pytest.mark.asyncio
async def test_empty_results_returns_empty_list() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{_API_BASE}/1").respond(
            200, text=json.dumps({"results": [], "count": 0})
        )

        scraper = AdzunaScraper(
            app_id=_FAKE_APP_ID,
            app_key=_FAKE_APP_KEY,
            query="biology",
            max_pages=2,
        )
        jobs = await scraper.fetch("Waterloo, ON")

    assert jobs == []


@pytest.mark.asyncio
async def test_http_error_on_first_page_returns_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{_API_BASE}/1").respond(500)

        scraper = AdzunaScraper(
            app_id=_FAKE_APP_ID,
            app_key=_FAKE_APP_KEY,
            query="biology",
            max_pages=2,
        )
        with caplog.at_level(logging.WARNING):
            jobs = await scraper.fetch("Waterloo, ON")

    assert jobs == []
    assert "Bad status" in caplog.text
