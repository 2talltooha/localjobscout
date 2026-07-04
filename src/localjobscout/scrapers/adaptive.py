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

from collections.abc import Callable, Mapping, Sequence
from typing import Protocol


class SelectorLike(Protocol):
    """Structural type for the slice of Scrapling's ``Selector`` API these
    helpers use. Scrapling is an optional dependency, so this module never
    imports it directly — structural typing lets mypy check call sites
    without requiring the real package to be installed."""

    @property
    def text(self) -> str | None: ...

    @property
    def attrib(self) -> Mapping[str, str]: ...

    def css(
        self,
        selector: str,
        identifier: str = "",
        adaptive: bool = False,
        auto_save: bool = False,
        percentage: int = 40,
    ) -> Sequence[SelectorLike]: ...

    def get_all_text(self) -> str: ...

    def has_class(self, class_name: str) -> bool: ...

    def find_ancestor(
        self, func: Callable[[SelectorLike], bool]
    ) -> SelectorLike | None: ...


def first(
    node: SelectorLike, css: str, *, identifier: str | None = None
) -> SelectorLike | None:
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


def first_text(
    node: SelectorLike, css: str, *, identifier: str | None = None
) -> str:
    el = first(node, css, identifier=identifier)
    if el is None or el.text is None:
        return ""
    return str(el.text).strip()


def all_matches(
    node: SelectorLike, css: str, *, identifier: str | None = None
) -> list[SelectorLike]:
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


def taleo_description(selector: SelectorLike, identifier: str) -> str:
    """Job-description body from a Taleo careers-portal detail Selector.

    Shared by the Taleo-hosted sites (uoguelph, laurier, hamiltonhealth) —
    identical markup, only the adaptive fingerprint identifier differs.
    """
    el = first(
        selector,
        'span[itemprop="description"] span.jobdescription',
        identifier=identifier,
    )
    if el is None:
        el = first(selector, "span.jobdescription")
    if el is None:
        return ""
    return " ".join(el.get_all_text().split()).strip()


def first_nonempty(
    node: SelectorLike, candidates: list[str], *, identifier: str | None = None
) -> list[SelectorLike]:
    """Try each css candidate in order; return the first non-empty match set.

    Mirrors the legacy ``soup.select(a) or soup.select(b) or ...`` fallback
    chain used by scrapers with uncertain/varying host markup. Each candidate
    gets its own identifier suffix (``{identifier}_{i}``) so adaptive
    relocation never returns a different candidate's fingerprinted element
    while this one is being tried.
    """
    for i, css in enumerate(candidates):
        cand_id = f"{identifier}_{i}" if identifier else None
        matches = all_matches(node, css, identifier=cand_id)
        if matches:
            return matches
    return []
