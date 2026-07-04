from __future__ import annotations

import pytest

pytest.importorskip("scrapling")

from scrapling.parser import Selector  # noqa: E402

from localjobscout.scrapers.cambridge import (  # noqa: E402
    extract_description_adaptive,
    extract_items_adaptive,
)

_BASE_URL = "https://www.cmh.org/careers"

_LISTING_HTML = """
<html><body>
<li class="views-row">
  <a href="/jobs/21">Lab Assistant</a>
  <span class="department">Laboratory</span>
</li>
<li class="views-row">
  <a href="/jobs/22">Food Server</a>
</li>
</body></html>
"""

_LONG_BODY = (
    "A reasonably long job description body for Cambridge Memorial Hospital posting."
)


def test_extract_items_adaptive_pulls_fields() -> None:
    sel = Selector(content=_LISTING_HTML, adaptive=True)
    seen: set[str] = set()
    items = extract_items_adaptive(sel, _BASE_URL, seen)
    assert len(items) == 2
    first = items[0]
    assert first["title"] == "Lab Assistant"
    assert first["href"] == "https://www.cmh.org/jobs/21"
    assert first["department"] == "Laboratory"


def test_extract_items_adaptive_dedupes_via_seen_set() -> None:
    sel = Selector(content=_LISTING_HTML, adaptive=True)
    seen: set[str] = {"https://www.cmh.org/jobs/21"}
    items = extract_items_adaptive(sel, _BASE_URL, seen)
    assert len(items) == 1
    assert items[0]["title"] == "Food Server"


def test_extract_items_adaptive_defaults_missing_department() -> None:
    sel = Selector(content=_LISTING_HTML, adaptive=True)
    seen: set[str] = set()
    items = extract_items_adaptive(sel, _BASE_URL, seen)
    assert items[1]["department"] == ""


def test_extract_description_adaptive_uses_first_matching_candidate() -> None:
    html = f'<html><body><div class="field--name-body">{_LONG_BODY}</div></body></html>'
    sel = Selector(content=html, adaptive=True)
    assert _LONG_BODY in extract_description_adaptive(sel)


def test_extract_description_adaptive_rejects_short_text() -> None:
    html = '<html><body><div class="field--name-body">short</div></body></html>'
    sel = Selector(content=html, adaptive=True)
    assert extract_description_adaptive(sel) == ""
