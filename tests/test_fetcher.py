from __future__ import annotations

from urllib.robotparser import RobotFileParser

import httpx
import pytest

from localjobscout.config import FetchConfig
from localjobscout.scrapers import base as scraper_base
from localjobscout.scrapers import fetcher, politeness


@pytest.fixture
def allow_all_robots(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass the robots.txt fetch so polite_get tests do no network I/O."""

    async def _permissive(_host: str) -> RobotFileParser:
        parser = RobotFileParser()
        parser.allow_all = True  # type: ignore[attr-defined]
        return parser

    monkeypatch.setattr(politeness, "get_robots", _permissive)


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


@pytest.mark.asyncio
async def test_fetch_selector_blocked_by_robots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fetch_selector is the only politeness gate for the adaptive scrapers
    (they call it directly, bypassing polite_get) — robots.txt must still be
    enforced here."""
    fetcher.configure(FetchConfig())
    monkeypatch.setattr(fetcher, "is_active", lambda: True)

    async def _disallow(_url: str, _user_agent: str = "") -> bool:
        return False

    monkeypatch.setattr(politeness, "can_fetch", _disallow)

    called = False

    async def _spy_raw(*_a: object, **_k: object) -> tuple[None, str, str]:
        nonlocal called
        called = True
        return None, "plain", ""

    monkeypatch.setattr(fetcher, "_fetch_raw", _spy_raw)

    try:
        result = await fetcher.fetch_selector(
            "https://example.com/blocked", source="jobbank"
        )
    finally:
        fetcher.reset()

    assert result is None
    assert called is False  # never dispatched the actual fetch


@pytest.mark.asyncio
async def test_fetch_selector_throttles_same_host(
    monkeypatch: pytest.MonkeyPatch,
    allow_all_robots: None,
) -> None:
    """Two fetch_selector calls to the same host go through politeness.throttle
    — the bug this closes is adaptive scrapers skipping rate limiting
    entirely by calling fetch_selector directly instead of polite_get."""
    fetcher.configure(FetchConfig())
    monkeypatch.setattr(fetcher, "is_active", lambda: True)

    class _FakePage:
        status = 200
        html_content = "<p>ok</p>"

    async def _fake_raw(*_a: object, **_k: object) -> tuple[object, str, str]:
        return _FakePage(), "plain", ""

    monkeypatch.setattr(fetcher, "_fetch_raw", _fake_raw)

    throttle_calls: list[str] = []
    real_throttle = politeness.throttle

    async def _spy_throttle(url: str, min_interval: float) -> None:
        throttle_calls.append(url)
        await real_throttle(url, min_interval)

    monkeypatch.setattr(politeness, "throttle", _spy_throttle)

    try:
        await fetcher.fetch_selector("https://jobbank.gc.ca/a", source="jobbank")
        await fetcher.fetch_selector("https://jobbank.gc.ca/b", source="jobbank")
    finally:
        fetcher.reset()

    assert len(throttle_calls) == 2
