from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from pytest_mock import MockerFixture

from localjobscout.db import make_job_id
from localjobscout.scrapers.indeed_pw import (
    IndeedPlaywrightScraper,
    extract_description_from_html,
)

_TARGET = "localjobscout.scrapers.indeed_pw.async_playwright"


def _install_mock_playwright(
    mocker: MockerFixture,
    *,
    title: str = "Indeed Jobs",
    evaluate_return: Any | None = None,
    evaluate_side_effect: Any | None = None,
    wait_for_selector_side_effect: Any | None = None,
) -> AsyncMock:
    mock_page = AsyncMock()
    mock_page.goto = AsyncMock(return_value=None)
    mock_page.title = AsyncMock(return_value=title)
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
    return mock_page


@pytest.mark.asyncio
async def test_happy_path(mocker: MockerFixture) -> None:
    page_data = [
        {
            "jk": "abc123def456",
            "title": "Research Assistant",
            "company": "Lab Co",
            "location": "Waterloo, ON",
            "snippet": "Wet lab work, 1+ yr experience",
        },
        {
            "jk": "bbb222ccc333",
            "title": "Biomedical Technician",
            "company": "Hospital",
            "location": "Kitchener, ON",
            "snippet": "Tissue processing",
        },
    ]
    _install_mock_playwright(
        mocker, evaluate_side_effect=[page_data, []]
    )

    jobs = await IndeedPlaywrightScraper(
        query="research assistant", max_pages=2
    ).fetch("Waterloo, ON")

    assert len(jobs) == 2
    assert all(j.source == "indeed" for j in jobs)

    research = next(j for j in jobs if j.title == "Research Assistant")
    expected_url = "https://ca.indeed.com/viewjob?jk=abc123def456"
    assert research.url == expected_url
    assert research.id == make_job_id("indeed", expected_url)
    assert research.company == "Lab Co"
    assert research.description == "Wet lab work, 1+ yr experience"


@pytest.mark.asyncio
async def test_cloudflare_challenge_short_circuits(
    mocker: MockerFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If page title is the Cloudflare interstitial, fetch returns [] and
    logs a warning — without ever calling wait_for_selector or evaluate."""
    page_data = [
        {"jk": "xyz", "title": "Real Job", "company": "", "location": "",
         "snippet": ""}
    ]
    mock_page = _install_mock_playwright(
        mocker,
        title="Just a moment...",
        evaluate_side_effect=[page_data],
    )

    import logging
    caplog.set_level(logging.WARNING, logger="localjobscout.scrapers.indeed_pw")

    jobs = await IndeedPlaywrightScraper(max_pages=2).fetch("Waterloo, ON")

    assert jobs == []
    mock_page.wait_for_selector.assert_not_called()
    mock_page.evaluate.assert_not_called()
    assert any(
        "Cloudflare" in r.getMessage() for r in caplog.records
    ), "expected a warning log mentioning Cloudflare"


@pytest.mark.asyncio
async def test_timeout_returns_empty_list(mocker: MockerFixture) -> None:
    _install_mock_playwright(
        mocker,
        wait_for_selector_side_effect=PlaywrightTimeoutError(
            "Timeout 20000ms exceeded."
        ),
    )

    jobs = await IndeedPlaywrightScraper(max_pages=2).fetch("Waterloo, ON")
    assert jobs == []


@pytest.mark.asyncio
async def test_no_cards_returns_empty_list(mocker: MockerFixture) -> None:
    _install_mock_playwright(mocker, evaluate_return=[])

    jobs = await IndeedPlaywrightScraper(max_pages=3).fetch("Waterloo, ON")
    assert jobs == []


@pytest.mark.asyncio
async def test_missing_jk_or_title_dropped(mocker: MockerFixture) -> None:
    page_data = [
        {"jk": "", "title": "No JK", "company": "", "location": "",
         "snippet": ""},
        {"jk": "valid1", "title": "", "company": "", "location": "",
         "snippet": ""},
        {"jk": "valid2", "title": "Good Job", "company": "Co",
         "location": "Loc", "snippet": "snippet"},
    ]
    _install_mock_playwright(mocker, evaluate_side_effect=[page_data, []])

    jobs = await IndeedPlaywrightScraper(max_pages=2).fetch("Waterloo, ON")
    assert len(jobs) == 1
    assert jobs[0].title == "Good Job"
    assert jobs[0].url == "https://ca.indeed.com/viewjob?jk=valid2"


_PRIMARY_DESC = "Full job description with at least thirty chars."
_FALLBACK_DESC = "Another description with at least thirty chars."


def test_extract_description_from_html_returns_text() -> None:
    html = (
        "<html><body>"
        f'<div id="jobDescriptionText">{_PRIMARY_DESC}</div>'
        "</body></html>"
    )
    result = extract_description_from_html(html)
    assert result == _PRIMARY_DESC


def test_extract_description_from_html_fallback_selector() -> None:
    html = (
        "<html><body>"
        f'<div class="jobsearch-jobDescriptionText">{_FALLBACK_DESC}</div>'
        "</body></html>"
    )
    result = extract_description_from_html(html)
    assert result == _FALLBACK_DESC


def test_extract_description_from_html_returns_empty_on_no_match() -> None:
    html = "<html><body><p>Nothing</p></body></html>"
    result = extract_description_from_html(html)
    assert result == ""


def test_extract_description_from_html_returns_empty_on_blank() -> None:
    assert extract_description_from_html("") == ""
    assert extract_description_from_html("   ") == ""


@pytest.mark.asyncio
async def test_description_populated_when_content_returns_html(
    mocker: MockerFixture,
) -> None:
    page_data = [
        {
            "jk": "abc123",
            "title": "Lab Assistant",
            "company": "Lab Inc",
            "location": "Waterloo, ON",
            "snippet": "short snippet",
        }
    ]
    desc_html = (
        "<html><body>"
        f'<div id="jobDescriptionText">{_PRIMARY_DESC}</div>'
        "</body></html>"
    )
    mock_page = _install_mock_playwright(
        mocker, evaluate_side_effect=[page_data, []]
    )
    mock_page.content = AsyncMock(return_value=desc_html)

    jobs = await IndeedPlaywrightScraper(max_pages=2).fetch("Waterloo, ON")

    assert len(jobs) == 1
    assert jobs[0].description == _PRIMARY_DESC


@pytest.mark.asyncio
async def test_description_falls_back_to_snippet_when_content_empty(
    mocker: MockerFixture,
) -> None:
    page_data = [
        {
            "jk": "xyz789",
            "title": "Research Assistant",
            "company": "Uni",
            "location": "Guelph, ON",
            "snippet": "original snippet text",
        }
    ]
    _install_mock_playwright(mocker, evaluate_side_effect=[page_data, []])
    # mock_page.content already returns "" from _install_mock_playwright

    jobs = await IndeedPlaywrightScraper(max_pages=2).fetch("Waterloo, ON")

    assert len(jobs) == 1
    assert jobs[0].description == "original snippet text"
