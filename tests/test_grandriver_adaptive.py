from __future__ import annotations

import pytest

pytest.importorskip("scrapling")

from scrapling.parser import Selector  # noqa: E402

from localjobscout.scrapers.grandriver import (  # noqa: E402
    extract_description_adaptive,
    extract_items_adaptive,
)

_BASE_URL = "https://www.grhosp.on.ca/grh/work-with-us/career-opportunities"

_LISTING_HTML = """
<html><body>
<li class="views-row">
  <a href="/jobs/77">RN - Med Surg</a>
  <span class="location">Kitchener, ON</span>
  <span class="department">Nursing</span>
</li>
<li class="views-row">
  <a href="/jobs/78">Porter</a>
</li>
</body></html>
"""

_LONG_BODY = (
    "A reasonably long job description body for grand river hospital posting here."
)


def test_extract_items_adaptive_pulls_fields() -> None:
    sel = Selector(content=_LISTING_HTML, adaptive=True)
    seen: set[str] = set()
    items = extract_items_adaptive(sel, _BASE_URL, seen)
    assert len(items) == 2
    first = items[0]
    assert first["title"] == "RN - Med Surg"
    assert first["href"] == "https://www.grhosp.on.ca/jobs/77"
    assert first["location"] == "Kitchener, ON"
    assert first["department"] == "Nursing"


def test_extract_items_adaptive_dedupes_via_seen_set() -> None:
    sel = Selector(content=_LISTING_HTML, adaptive=True)
    seen: set[str] = {"https://www.grhosp.on.ca/jobs/77"}
    items = extract_items_adaptive(sel, _BASE_URL, seen)
    assert len(items) == 1
    assert items[0]["title"] == "Porter"


def test_extract_items_adaptive_defaults_missing_location() -> None:
    sel = Selector(content=_LISTING_HTML, adaptive=True)
    seen: set[str] = set()
    items = extract_items_adaptive(sel, _BASE_URL, seen)
    second = items[1]
    assert second["location"] == "Kitchener/Waterloo, ON"
    assert second["department"] == ""


def test_extract_description_adaptive_uses_first_matching_candidate() -> None:
    html = f'<html><body><div class="field--name-body">{_LONG_BODY}</div></body></html>'
    sel = Selector(content=html, adaptive=True)
    assert _LONG_BODY in extract_description_adaptive(sel)


def test_extract_description_adaptive_rejects_short_text() -> None:
    html = '<html><body><div class="field--name-body">short</div></body></html>'
    sel = Selector(content=html, adaptive=True)
    assert extract_description_adaptive(sel) == ""


def test_extract_description_adaptive_empty_when_no_candidate_matches() -> None:
    sel = Selector(content="<html><body><p>nothing</p></body></html>", adaptive=True)
    assert extract_description_adaptive(sel) == ""
