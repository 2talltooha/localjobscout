"""Shared adaptive (self-healing) extraction helpers for Scrapling Selectors.

A handful of brittle, layout-sensitive selectors (card/row lists, detail
description blocks) are tagged with a stable identifier and
``adaptive=True``/``auto_save=True`` so Scrapling relocates them by
fingerprint when a job board changes its markup. Field sub-selects within an
already-located node stay plain (no identifier) — see ``scrapers/jobbank.py``
for the original Phase 2 prototype this generalizes to the remaining httpx
sources.
"""
from __future__ import annotations

from typing import Any


def first(node: Any, css: str, *, identifier: str | None = None) -> Any | None:
    """First element matching css, or None. Adaptive when identifier given."""
    try:
        res = (
            node.css(css, identifier=identifier, adaptive=True, auto_save=True)
            if identifier
            else node.css(css)
        )
    except TypeError:
        res = node.css(css)
    return res[0] if res else None


def first_text(node: Any, css: str, *, identifier: str | None = None) -> str:
    el = first(node, css, identifier=identifier)
    if el is None or el.text is None:
        return ""
    return str(el.text).strip()


def all_matches(node: Any, css: str, *, identifier: str | None = None) -> list[Any]:
    """All elements matching css, in document order. Adaptive when identifier given."""
    try:
        res = (
            node.css(css, identifier=identifier, adaptive=True, auto_save=True)
            if identifier
            else node.css(css)
        )
    except TypeError:
        res = node.css(css)
    return list(res) if res else []


def first_nonempty(
    node: Any, candidates: list[str], *, identifier: str | None = None
) -> list[Any]:
    """Try each css candidate in order; return the first non-empty match set.

    Mirrors the legacy ``soup.select(a) or soup.select(b) or ...`` fallback
    chain used by scrapers with uncertain/varying host markup. All candidates
    share one identifier so adaptive relocation tracks whichever selector
    matched.
    """
    for css in candidates:
        matches = all_matches(node, css, identifier=identifier)
        if matches:
            return matches
    return []
