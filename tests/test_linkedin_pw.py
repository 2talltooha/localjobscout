from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from pytest_mock import MockerFixture

from localjobscout.db import make_job_id
from localjobscout.scrapers.linkedin_pw import (
    LinkedInPlaywrightScraper,
    extract_description_from_html,
)

_TARGET = "localjobscout.scrapers.linkedin_pw.async_playwright"


def _install_mock_playwright(
    mocker: MockerFixture,
    *,
    evaluate_return: Any | None = None,
    evaluate_side_effect: Any | None = None,
    wait_for_selector_side_effect: Any | None = None,
) -> tuple[AsyncMock, AsyncMock]:
    """Patch `async_playwright` to return a fake context that yields a page
    whose `evaluate` and `wait_for_selector` behave as configured.

    Returns (mock_page, mock_browser) so individual tests can introspect call
    counts and arguments.
    """
    mock_page = AsyncMock()
    mock_page.goto = AsyncMock(return_value=None)
    # content() returns empty HTML so description-fetch gracefully yields ""
    mock_page.content = AsyncMock(return_value="")
    if wait_for_selector_side_effect is not None:
        mock_page.wait_for_selector = AsyncMock(
            side_effect=wait_for_selector_side_effect
        )
    else:
        mock_page.wait_for_selector = AsyncMock(return_value=None)

    if evaluate_side_effect is not None:
        mock_page.evaluate = AsyncMock(side_effect=evaluate_side_effect)
    else:
        mock_page.evaluate = AsyncMock(return_value=evaluate_return or [])

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock(return_value=None)

    mock_pw_obj = MagicMock()
    mock_pw_obj.chromium = MagicMock()
    mock_pw_obj.chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw_ctx = AsyncMock()
    mock_pw_ctx.__aenter__ = AsyncMock(return_value=mock_pw_obj)
    mock_pw_ctx.__aexit__ = AsyncMock(return_value=None)

    mocker.patch(_TARGET, return_value=mock_pw_ctx)
    return mock_page, mock_browser


@pytest.mark.asyncio
async def test_happy_path_two_cards(mocker: MockerFixture) -> None:
    """A page with 2 fake cards → 2 jobs returned with normalised URLs."""
    page_data = [
        {
            "url": "https://ca.linkedin.com/jobs/view/research-assistant-at-co-1234567",
            "title": "Research Assistant",
            "company": "Test Co",
            "location": "Waterloo, Ontario, Canada",
            "posted_at": "2026-05-10",
        },
        {
            "url": "https://www.linkedin.com/jobs/view/lab-tech-7654321/?ref=foo",
            "title": "Lab Technician",
            "company": "Lab Co",
            "location": "Kitchener, Ontario, Canada",
            "posted_at": "",
        },
    ]
    _install_mock_playwright(
        mocker,
        evaluate_side_effect=[page_data, []],
    )

    jobs = await LinkedInPlaywrightScraper(
        query="research", max_pages=2
    ).fetch("Waterloo, ON")

    assert len(jobs) == 2
    assert all(j.source == "linkedin" for j in jobs)

    research = next(j for j in jobs if j.title == "Research Assistant")
    assert research.company == "Test Co"
    assert research.posted_at == "2026-05-10"
    expected_url = "https://www.linkedin.com/jobs/view/1234567/"
    assert research.url == expected_url
    assert research.id == make_job_id("linkedin", expected_url)

    lab = next(j for j in jobs if j.title == "Lab Technician")
    assert lab.url == "https://www.linkedin.com/jobs/view/7654321/"
    assert lab.posted_at is None


@pytest.mark.asyncio
async def test_timeout_returns_empty_list(mocker: MockerFixture) -> None:
    """wait_for_selector raises TimeoutError → return [], do not raise."""
    _install_mock_playwright(
        mocker,
        wait_for_selector_side_effect=PlaywrightTimeoutError(
            "Timeout 15000ms exceeded."
        ),
    )

    jobs = await LinkedInPlaywrightScraper(max_pages=2).fetch("Waterloo, ON")
    assert jobs == []


@pytest.mark.asyncio
async def test_no_cards_returns_empty_list(mocker: MockerFixture) -> None:
    """Page evaluate returns [] → fetch returns [] without crashing."""
    _install_mock_playwright(mocker, evaluate_return=[])

    jobs = await LinkedInPlaywrightScraper(max_pages=3).fetch("Waterloo, ON")
    assert jobs == []


@pytest.mark.asyncio
async def test_duplicate_urls_deduped(mocker: MockerFixture) -> None:
    """Same job posting appearing on consecutive pages → counted once."""
    page = [
        {
            "url": "https://ca.linkedin.com/jobs/view/foo-1111",
            "title": "Same Job",
            "company": "Co",
            "location": "Waterloo, ON",
            "posted_at": "",
        }
    ]
    _install_mock_playwright(mocker, evaluate_side_effect=[page, page])

    jobs = await LinkedInPlaywrightScraper(max_pages=2).fetch("Waterloo, ON")
    assert len(jobs) == 1


@pytest.mark.asyncio
async def test_missing_title_or_url_dropped(mocker: MockerFixture) -> None:
    """Cards without a URL or title are silently skipped."""
    page_data = [
        {"url": "", "title": "Has Title No URL", "company": "", "location": "",
         "posted_at": ""},
        {"url": "https://ca.linkedin.com/jobs/view/no-title-9999",
         "title": "", "company": "", "location": "", "posted_at": ""},
        {"url": "https://ca.linkedin.com/jobs/view/good-2222",
         "title": "Good Job", "company": "X", "location": "Y",
         "posted_at": ""},
    ]
    _install_mock_playwright(
        mocker, evaluate_side_effect=[page_data, []]
    )

    jobs = await LinkedInPlaywrightScraper(max_pages=2).fetch("Waterloo, ON")
    assert len(jobs) == 1
    assert jobs[0].title == "Good Job"


# ─── Description extraction (pure-function, no Playwright) ───────────────────

_FIXTURE_HTML = (
    Path(__file__).parent / "fixtures" / "linkedin_job_page.html"
)


def test_extract_description_from_fixture_html() -> None:
    """Fixture HTML contains 'Research Assistant' description — parse it."""
    html = _FIXTURE_HTML.read_text(encoding="utf-8")
    desc = extract_description_from_html(html)
    assert len(desc) > 30
    assert "Research Assistant" in desc or "laboratory" in desc.lower()


def test_extract_description_primary_selector() -> None:
    """show-more-less-html__markup is tried first and should win."""
    html = """
    <html><body>
    <div class="show-more-less-html__markup">Primary content here. Long enough.</div>
    <div class="description__text">Secondary content here. Also long enough.</div>
    </body></html>
    """
    desc = extract_description_from_html(html)
    assert "Primary content" in desc
    assert "Secondary content" not in desc


def test_extract_description_fallback_selector() -> None:
    """Falls back to description__text when primary selector absent."""
    html = """
    <html><body>
    <div class="description__text">Fallback description text here for this role.</div>
    </body></html>
    """
    desc = extract_description_from_html(html)
    assert "Fallback description" in desc


def test_extract_description_empty_html_returns_empty() -> None:
    assert extract_description_from_html("") == ""
    assert extract_description_from_html("   ") == ""


def test_extract_description_no_matching_selector_returns_empty() -> None:
    html = "<html><body><p>Some unrelated page content.</p></body></html>"
    assert extract_description_from_html(html) == ""


def test_extract_description_short_match_skipped() -> None:
    """A match with < 30 chars is not returned."""
    html = """
    <html><body>
    <div class="show-more-less-html__markup">Too short.</div>
    </body></html>
    """
    assert extract_description_from_html(html) == ""


@pytest.mark.asyncio
async def test_description_populated_when_content_returns_html(
    mocker: MockerFixture,
) -> None:
    """When page.content() returns real HTML, description is populated."""
    fixture_html = _FIXTURE_HTML.read_text(encoding="utf-8")
    page_data = [
        {
            "url": "https://ca.linkedin.com/jobs/view/research-asst-9999",
            "title": "Research Assistant",
            "company": "U of G",
            "location": "Guelph, ON",
            "posted_at": "",
        }
    ]
    mock_page, _ = _install_mock_playwright(
        mocker,
        evaluate_side_effect=[page_data, []],
    )
    # Override content() to return fixture HTML for description fetch
    mock_page.content = AsyncMock(return_value=fixture_html)

    jobs = await LinkedInPlaywrightScraper(max_pages=1).fetch("Waterloo, ON")
    assert len(jobs) == 1
    assert len(jobs[0].description) > 30
    assert "Research" in jobs[0].description
