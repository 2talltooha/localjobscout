"""Page-fetch adapter — one interface, swappable engine (Phase 1).

The rest of the codebase fetches HTML through a single entry point,
``fetch_page(url)``, so the engine behind it can change without touching any
scraper. Phase 1 swaps only the transport (Scrapling vs. the legacy
httpx/Playwright path); selectors and parsing are untouched.

Engines
-------
- ``plain``   → Scrapling ``Fetcher``        (fast HTTP, good default)
- ``stealth`` → Scrapling ``StealthyFetcher`` (real browser, beats Cloudflare)

Safety / fallback
-----------------
- Scrapling is an optional dependency (``localjobscout[fetch]``). When it is
  not installed, ``fetch_page`` returns ``FetchResult(ok=False, ...)`` and the
  caller (``base.polite_get`` / the test command) falls back to the built-in
  fetch path — the pipeline never hard-breaks.
- The adapter is *off* until ``configure()`` is called with an enabled
  ``FetchConfig`` (done by ``run_scan`` and the ``--fetch-test`` command). This
  keeps unit tests that mock httpx with respx on the legacy path by default.
- Any engine exception is caught and surfaced as ``ok=False`` so the caller
  can fall back rather than crash.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from localjobscout.config import FetchConfig

logger = logging.getLogger(__name__)

# ── Optional Scrapling import ────────────────────────────────────────────────
_Fetcher: Any = None
_StealthyFetcher: Any = None
SCRAPLING_AVAILABLE = False
try:  # pragma: no cover - import shape varies across scrapling versions
    from scrapling.fetchers import Fetcher as _Fetcher
    from scrapling.fetchers import StealthyFetcher as _StealthyFetcher

    SCRAPLING_AVAILABLE = True
except ImportError:
    try:  # pragma: no cover
        from scrapling import Fetcher as _Fetcher
        from scrapling import StealthyFetcher as _StealthyFetcher

        SCRAPLING_AVAILABLE = True
    except ImportError:
        SCRAPLING_AVAILABLE = False


# Module-global config. None = adapter disabled (legacy path). Set via
# configure(); cleared via reset() (tests).
_config: FetchConfig | None = None


def configure(config: FetchConfig | None) -> None:
    """Enable/disable the adapter and set engine policy. Call once per run."""
    global _config
    _config = config


def reset() -> None:
    """Disable the adapter (used by tests to guarantee the legacy path)."""
    global _config
    _config = None


def is_active() -> bool:
    """True when Scrapling routing should be attempted for this process."""
    return SCRAPLING_AVAILABLE and _config is not None and _config.enabled


def should_bypass(source: str | None) -> bool:
    """True when a source is pinned to the legacy fetch path (never Scrapling).

    Used for JSON-API sources whose raw body Scrapling wraps in HTML.
    """
    return bool(source and _config is not None and source in _config.legacy_sources)


@dataclass(frozen=True)
class FetchResult:
    """Outcome of one fetch attempt through the adapter."""

    url: str
    ok: bool
    status: int = 0
    html: str | None = None
    engine_used: str = ""
    reason: str = ""


def resolve_engine(source: str | None, *, override: str | None = None) -> str:
    """Decide which engine to use for a source.

    Precedence: explicit override → per-source config → auto rule → plain.
    """
    if override in ("plain", "stealth"):
        return override
    cfg = _config
    if cfg is None:
        return "plain"
    if source and source in cfg.source_engines:
        return cfg.source_engines[source]
    if cfg.default_engine in ("plain", "stealth"):
        return cfg.default_engine
    # auto
    if source and source in cfg.stealth_sources:
        return "stealth"
    return "plain"


def _extract_html(page: Any) -> str:
    """Pull HTML text out of a Scrapling response across versions."""
    for attr in ("html_content", "body"):
        value = getattr(page, attr, None)
        if isinstance(value, str) and value:
            return value
    try:
        return str(page)
    except Exception:  # noqa: BLE001
        return ""


def _extract_status(page: Any) -> int:
    status = getattr(page, "status", None)
    return int(status) if isinstance(status, int) else 200


def _plain_fetch(url: str, timeout: float) -> Any:
    """Blocking Scrapling Fetcher.get with version-tolerant kwargs."""
    try:
        return _Fetcher.get(url, timeout=int(timeout), stealthy_headers=True)
    except TypeError:
        # Older/newer signatures: retry without optional kwargs, then as
        # an instance method if .get is not a classmethod.
        try:
            return _Fetcher.get(url)
        except TypeError:
            return _Fetcher().get(url)


def _stealth_fetch(url: str, timeout: float) -> Any:
    """Blocking Scrapling StealthyFetcher.fetch with version-tolerant kwargs."""
    try:
        return _StealthyFetcher.fetch(
            url, headless=True, network_idle=True, timeout=int(timeout) * 1000
        )
    except TypeError:
        try:
            return _StealthyFetcher.fetch(url)
        except TypeError:
            return _StealthyFetcher().fetch(url)


async def fetch_page(
    url: str,
    *,
    source: str | None = None,
    engine: str | None = None,
    timeout: float | None = None,
) -> FetchResult:
    """Fetch a page via the configured Scrapling engine.

    Returns ``ok=False`` (never raises) when the adapter is inactive or the
    engine fails, so callers can fall back to the legacy fetch path.
    """
    if not is_active():
        return FetchResult(
            url=url, ok=False, reason="adapter inactive (scrapling off/missing)"
        )

    eng = resolve_engine(source, override=engine)
    to = timeout if timeout is not None else (
        _config.timeout if _config is not None else 20.0
    )

    fn = _stealth_fetch if eng == "stealth" else _plain_fetch
    try:
        page = await asyncio.to_thread(fn, url, to)
    except Exception as exc:  # noqa: BLE001 - any engine failure → fallback
        logger.warning("scrapling %s fetch failed for %s: %s", eng, url, exc)
        return FetchResult(
            url=url, ok=False, engine_used=eng,
            reason=f"{type(exc).__name__}: {exc}",
        )

    html = _extract_html(page)
    status = _extract_status(page)
    ok = bool(html) and status < 400
    return FetchResult(
        url=url, ok=ok, status=status, html=html if html else None,
        engine_used=eng,
        reason="" if ok else f"empty html or status {status}",
    )
