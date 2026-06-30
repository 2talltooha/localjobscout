from __future__ import annotations

from urllib.robotparser import RobotFileParser

import httpx
import pytest

from localjobscout.config import FetchConfig
from localjobscout.scrapers import base as scraper_base
from localjobscout.scrapers import fetcher


@pytest.fixture
def allow_all_robots(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass the robots.txt fetch so polite_get tests do no network I/O."""

    async def _permissive(_client: object, _host: str) -> RobotFileParser:
        parser = RobotFileParser()
        parser.allow_all = True  # type: ignore[attr-defined]
        return parser

    monkeypatch.setattr(scraper_base, "_get_robots", _permissive)


def test_inactive_by_default() -> None:
    # reset_state autouse fixture calls fetcher.reset() → adapter off.
    assert fetcher.is_active() is False


@pytest.mark.asyncio
async def test_fetch_page_inactive_returns_not_ok() -> None:
    result = await fetcher.fetch_page("https://example.com")
    assert result.ok is False
    assert "inactive" in result.reason


def test_resolve_engine_auto_plain_for_unknown_source() -> None:
    fetcher.configure(FetchConfig())
    try:
        assert fetcher.resolve_engine("jobbank") == "plain"
    finally:
        fetcher.reset()


def test_resolve_engine_auto_stealth_for_stealth_sources() -> None:
    fetcher.configure(FetchConfig(stealth_sources=["indeed"]))
    try:
        assert fetcher.resolve_engine("indeed") == "stealth"
    finally:
        fetcher.reset()


def test_resolve_engine_source_override_wins() -> None:
    fetcher.configure(FetchConfig(source_engines={"talent": "stealth"}))
    try:
        assert fetcher.resolve_engine("talent") == "stealth"
    finally:
        fetcher.reset()


def test_resolve_engine_explicit_override_wins() -> None:
    fetcher.configure(FetchConfig(stealth_sources=["indeed"]))
    try:
        # Explicit per-call override beats the stealth_sources rule.
        assert fetcher.resolve_engine("indeed", override="plain") == "plain"
    finally:
        fetcher.reset()


def test_resolve_engine_default_engine_forces_value() -> None:
    fetcher.configure(FetchConfig(default_engine="stealth"))
    try:
        assert fetcher.resolve_engine("jobbank") == "stealth"
    finally:
        fetcher.reset()


@pytest.mark.asyncio
async def test_polite_get_wraps_scrapling_result(
    monkeypatch: pytest.MonkeyPatch,
    allow_all_robots: None,
) -> None:
    """When the adapter succeeds, polite_get returns an httpx.Response built
    from the Scrapling HTML — callers see the same .text/.status_code."""
    monkeypatch.setattr(fetcher, "is_active", lambda: True)

    async def _fake_fetch_page(url: str, **_: object) -> fetcher.FetchResult:
        return fetcher.FetchResult(
            url=url, ok=True, status=200, html="<p>scrapling body</p>",
            engine_used="plain",
        )

    monkeypatch.setattr(fetcher, "fetch_page", _fake_fetch_page)

    async with httpx.AsyncClient() as client:
        resp = await scraper_base.polite_get(client, "https://jobbank.gc.ca/x")

    assert resp is not None
    assert resp.status_code == 200
    assert resp.text == "<p>scrapling body</p>"


@pytest.mark.asyncio
async def test_polite_get_falls_back_when_adapter_fails(
    monkeypatch: pytest.MonkeyPatch,
    allow_all_robots: None,
) -> None:
    """When the adapter returns not-ok, polite_get falls back to the httpx
    GET path (here served by a stubbed client)."""
    monkeypatch.setattr(fetcher, "is_active", lambda: True)

    async def _fail_fetch_page(url: str, **_: object) -> fetcher.FetchResult:
        return fetcher.FetchResult(url=url, ok=False, reason="boom")

    monkeypatch.setattr(fetcher, "fetch_page", _fail_fetch_page)

    calls: list[str] = []

    class _StubClient:
        async def get(self, url: str, **_: object) -> httpx.Response:
            calls.append(url)
            return httpx.Response(status_code=200, html="<p>httpx body</p>")

    resp = await scraper_base.polite_get(
        _StubClient(),  # type: ignore[arg-type]
        "https://jobbank.gc.ca/y",
    )

    assert calls == ["https://jobbank.gc.ca/y"]
    assert resp is not None
    assert resp.text == "<p>httpx body</p>"
