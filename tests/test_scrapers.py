from __future__ import annotations

from pathlib import Path

import pytest
import respx

from localjobscout.db import make_job_id
from localjobscout.scrapers.jobbank import JobBankScraper

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_HOST = "www.jobbank.gc.ca"
_ROBOTS_URL = f"https://{_HOST}/robots.txt"
_SEARCH_URL = f"https://{_HOST}/jobsearch/jobsearch"
_ROBOTS_ALLOW = "User-agent: *\nAllow: /"
_ROBOTS_BLOCK = "User-agent: *\nDisallow: /jobsearch/"

_FIXTURES = Path(__file__).parent / "fixtures"

_PAGE1_HTML = (_FIXTURES / "jobbank_search_page1.html").read_text(encoding="utf-8")
_PAGE2_HTML = (_FIXTURES / "jobbank_search_page2.html").read_text(encoding="utf-8")
_DETAIL_HTML = (_FIXTURES / "jobbank_detail.html").read_text(encoding="utf-8")

_EMPTY_PAGE_HTML = (
    "<!DOCTYPE html><html><body>"
    "<section id='searchResults'></section>"
    "</body></html>"
)

_SIMPLE_DETAIL_HTML = (
    "<html><body>"
    "<div id='jobDescriptionId'>Sample description.</div>"
    "<time datetime='2025-11-01'>Nov 1</time>"
    "</body></html>"
)

_LOCATION = "Waterloo ON"
# Build search URLs exactly as JobBankScraper does, then let httpx normalise.
_SEARCH_P1 = f"{_SEARCH_URL}?searchstring=&locationstring={_LOCATION}"
_SEARCH_P2 = _SEARCH_P1 + "&page=2"
_SEARCH_P3 = _SEARCH_P1 + "&page=3"

# Detail URLs: urljoin of the search page URL + href from fixtures
_DETAIL_URL_1 = f"https://{_HOST}/en/job/software-developer-123456"
_DETAIL_URL_2 = f"https://{_HOST}/en/job/data-analyst-789012"
_DETAIL_URL_3 = f"https://{_HOST}/en/job/devops-engineer-345678"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pagination_happy_path() -> None:
    """Page 1 → 2 valid cards (+1 skipped for missing href), page 2 → 1 card,
    page 3 → empty. Expect exactly 3 jobs with deterministic source-prefixed IDs."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_ALLOW)
        mock.get(_SEARCH_P1).respond(200, text=_PAGE1_HTML)
        mock.get(_SEARCH_P2).respond(200, text=_PAGE2_HTML)
        mock.get(_SEARCH_P3).respond(200, text=_EMPTY_PAGE_HTML)
        mock.get(_DETAIL_URL_1).respond(200, text=_DETAIL_HTML)
        mock.get(_DETAIL_URL_2).respond(200, text=_SIMPLE_DETAIL_HTML)
        mock.get(_DETAIL_URL_3).respond(200, text=_SIMPLE_DETAIL_HTML)

        jobs = await JobBankScraper(max_pages=3).fetch(_LOCATION)

    assert len(jobs) == 3
    assert all(j.source == "jobbank" for j in jobs)
    expected_ids = {
        make_job_id("jobbank", _DETAIL_URL_1),
        make_job_id("jobbank", _DETAIL_URL_2),
        make_job_id("jobbank", _DETAIL_URL_3),
    }
    assert {j.id for j in jobs} == expected_ids


@pytest.mark.asyncio
async def test_empty_first_page() -> None:
    """Empty results on page 1 → stop immediately; no detail requests."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_ALLOW)
        search_route = mock.get(_SEARCH_P1).respond(200, text=_EMPTY_PAGE_HTML)

        jobs = await JobBankScraper().fetch(_LOCATION)

        # robots.txt + search page 1 = 2; capture before context exit clears the list
        total_calls = len(mock.calls)

    assert jobs == []
    assert search_route.call_count == 1
    assert total_calls == 2


@pytest.mark.asyncio
async def test_robots_blocks_us() -> None:
    """robots.txt disallows /jobsearch/ → fetch() returns [] with no search requests."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_BLOCK)
        search_route = mock.get(_SEARCH_P1).respond(200, text=_PAGE1_HTML)

        jobs = await JobBankScraper().fetch(_LOCATION)

    assert jobs == []
    assert search_route.call_count == 0


@pytest.mark.asyncio
async def test_rate_limit() -> None:
    """429 on first search page → return [] without fetching further pages."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_ALLOW)
        search_p1_route = mock.get(_SEARCH_P1).respond(429)
        search_p2_route = mock.get(_SEARCH_P2).respond(200, text=_PAGE2_HTML)

        jobs = await JobBankScraper().fetch(_LOCATION)

    assert jobs == []
    assert search_p1_route.call_count == 1
    assert search_p2_route.call_count == 0


@pytest.mark.asyncio
async def test_detail_failure_isolated() -> None:
    """500 on one detail page → that job has empty description; other job unaffected."""
    two_card_html = (
        "<!DOCTYPE html><html><body><main>"
        "<section id='searchResults'>"
        "<article>"
        "  <a class='resultJobItem' href='/en/job/software-developer-123456'>"
        "    <span class='noctitle'>Software Developer</span>"
        "  </a>"
        "</article>"
        "<article>"
        "  <a class='resultJobItem' href='/en/job/data-analyst-789012'>"
        "    <span class='noctitle'>Data Analyst</span>"
        "  </a>"
        "</article>"
        "</section></main></body></html>"
    )

    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_ALLOW)
        mock.get(_SEARCH_P1).respond(200, text=two_card_html)
        mock.get(_SEARCH_P2).respond(200, text=_EMPTY_PAGE_HTML)
        mock.get(_DETAIL_URL_1).respond(200, text=_DETAIL_HTML)
        mock.get(_DETAIL_URL_2).respond(500)

        jobs = await JobBankScraper(max_pages=2).fetch(_LOCATION)

    assert len(jobs) == 2
    failed = next(j for j in jobs if j.id == make_job_id("jobbank", _DETAIL_URL_2))
    assert failed.description == ""


@pytest.mark.asyncio
async def test_malformed_card_skipped() -> None:
    """Card missing .noctitle is silently dropped; valid sibling card is returned."""
    mixed_html = (
        "<!DOCTYPE html><html><body><main>"
        "<section id='searchResults'>"
        "<article>"
        "  <a class='resultJobItem' href='/en/job/software-developer-123456'>"
        "    <span class='noctitle'>Software Developer</span>"
        "  </a>"
        "</article>"
        "<article>"
        "  <a class='resultJobItem' href='/en/job/bad-card-000000'>"
        "    <span class='business'>Ghost Corp</span>"
        "  </a>"
        "</article>"
        "</section></main></body></html>"
    )

    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_ALLOW)
        mock.get(_SEARCH_P1).respond(200, text=mixed_html)
        mock.get(_SEARCH_P2).respond(200, text=_EMPTY_PAGE_HTML)
        mock.get(_DETAIL_URL_1).respond(200, text=_DETAIL_HTML)

        jobs = await JobBankScraper(max_pages=2).fetch(_LOCATION)

    assert len(jobs) == 1
    assert jobs[0].title == "Software Developer"


@pytest.mark.asyncio
async def test_max_pages_respected() -> None:
    """max_pages=2 → exactly 2 search requests; third page never fetched."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_ALLOW)
        p1_route = mock.get(_SEARCH_P1).respond(200, text=_PAGE1_HTML)
        p2_route = mock.get(_SEARCH_P2).respond(200, text=_PAGE2_HTML)
        p3_route = mock.get(_SEARCH_P3).respond(200, text=_PAGE1_HTML)
        mock.get(_DETAIL_URL_1).respond(200, text=_DETAIL_HTML)
        mock.get(_DETAIL_URL_2).respond(200, text=_SIMPLE_DETAIL_HTML)
        mock.get(_DETAIL_URL_3).respond(200, text=_SIMPLE_DETAIL_HTML)

        await JobBankScraper(max_pages=2).fetch(_LOCATION)

    assert p1_route.call_count == 1
    assert p2_route.call_count == 1
    assert p3_route.call_count == 0
