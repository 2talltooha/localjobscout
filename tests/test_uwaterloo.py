from __future__ import annotations

import logging

import httpx
import pytest
import respx

from localjobscout.db import make_job_id
from localjobscout.scrapers.uwaterloo import (
    _BASE_JOB_URL,
    _CXS_URL,
    UWaterlooScraper,
)


def _posting(
    *,
    title: str,
    path: str,
    locations: str = "Waterloo, Ontario, Canada",
    posted: str = "Posted Yesterday",
) -> dict[str, object]:
    return {
        "title": title,
        "externalPath": path,
        "locationsText": locations,
        "postedOn": posted,
    }


@pytest.mark.asyncio
async def test_happy_path_single_page() -> None:
    body = {
        "total": 2,
        "jobPostings": [
            _posting(
                title="Research Assistant II",
                path="/job/Waterloo-Ontario-Canada/Research-Assistant-II_2026-00358",
            ),
            _posting(
                title="Lab Technician",
                path="/job/Waterloo/Lab-Tech_R-999",
                locations="Cambridge, Ontario, Canada",
                posted="Posted 3 Days Ago",
            ),
        ],
    }

    with respx.mock(assert_all_called=False) as mock:
        mock.post(_CXS_URL).respond(200, json=body)
        jobs = await UWaterlooScraper(max_pages=1, query="research assistant").fetch(
            "Waterloo, ON"
        )

    assert len(jobs) == 2
    assert all(j.source == "uwaterloo" for j in jobs)
    assert all(j.company == "University of Waterloo" for j in jobs)
    assert all(j.notified is False for j in jobs)
    assert all(j.score is None for j in jobs)

    u0 = f"{_BASE_JOB_URL}/job/Waterloo-Ontario-Canada/Research-Assistant-II_2026-00358"
    assert jobs[0].title == "Research Assistant II"
    assert jobs[0].url == u0
    assert jobs[0].id == make_job_id("uwaterloo", u0)
    assert jobs[0].location == "Waterloo, Ontario, Canada"
    assert jobs[0].posted_at == "Posted Yesterday"
    assert "Research Assistant II" in jobs[0].description

    u1 = f"{_BASE_JOB_URL}/job/Waterloo/Lab-Tech_R-999"
    assert jobs[1].url == u1
    assert jobs[1].posted_at == "Posted 3 Days Ago"


@pytest.mark.asyncio
async def test_pagination_stops_at_max_pages() -> None:
    page1 = {
        "total": 50,
        "jobPostings": [_posting(title="Only Page One", path="/job/A/R1")],
    }
    page2 = {
        "total": 50,
        "jobPostings": [_posting(title="Should Not Fetch", path="/job/A/R2")],
    }

    post_count = {"n": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        post_count["n"] += 1
        if post_count["n"] == 1:
            return httpx.Response(200, json=page1)
        return httpx.Response(200, json=page2)

    with respx.mock(assert_all_called=False) as mock:
        mock.post(_CXS_URL).mock(side_effect=responder)
        jobs = await UWaterlooScraper(max_pages=1, query="ra").fetch("any")

    assert post_count["n"] == 1
    assert len(jobs) == 1
    assert jobs[0].title == "Only Page One"


@pytest.mark.asyncio
async def test_empty_job_postings_returns_empty_list() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.post(_CXS_URL).respond(200, json={"jobPostings": [], "total": 0})
        jobs = await UWaterlooScraper(max_pages=2, query="x").fetch("y")

    assert jobs == []


@pytest.mark.asyncio
async def test_http_error_returns_empty_list(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.post(_CXS_URL).respond(503)
        with caplog.at_level(logging.WARNING):
            jobs = await UWaterlooScraper(max_pages=1, query="q").fetch("loc")

    assert jobs == []
    assert "503" in caplog.text


@pytest.mark.asyncio
async def test_missing_external_path_skips_entry() -> None:
    body = {
        "total": 2,
        "jobPostings": [
            {"title": "No Path Job", "locationsText": "Waterloo, ON"},
            _posting(title="Valid Job", path="/job/OK/real"),
        ],
    }

    with respx.mock(assert_all_called=False) as mock:
        mock.post(_CXS_URL).respond(200, json=body)
        jobs = await UWaterlooScraper(max_pages=1).fetch("Waterloo, ON")

    assert len(jobs) == 1
    assert jobs[0].title == "Valid Job"
