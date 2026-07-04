from __future__ import annotations

import pytest

pytest.importorskip("scrapling")

from scrapling.parser import Selector  # noqa: E402

from localjobscout.scrapers.uoguelph import (  # noqa: E402
    extract_description_adaptive,
    extract_tiles_adaptive,
)

_SEARCH_HTML = """
<html><body>
<li class="job-tile">
  <a class="jobTitle-link" href="/job/101">Research Assistant</a>
  <div class="section-field location"><span>Location</span><div>Guelph, ON</div></div>
  <div class="section-field facility"><span>Facility</span><div>CBS</div></div>
  <div class="section-field dept"><span>Dept</span><div>Biology</div></div>
</li>
<li class="job-tile">
  <a class="jobTitle-link" href="/job/102">Lab Technician</a>
  <div class="section-field location"><span>Location</span><div>Guelph, ON</div></div>
</li>
</body></html>
"""

_DETAIL_HTML = """
<html><body>
<span itemprop="description"><span class="jobdescription">
Assist with plant sample prep and greenhouse trials.
</span></span>
</body></html>
"""


def test_extract_tiles_adaptive_pulls_fields() -> None:
    sel = Selector(content=_SEARCH_HTML, adaptive=True)
    tiles = extract_tiles_adaptive(sel)
    assert len(tiles) == 2
    first = tiles[0]
    assert first["title"] == "Research Assistant"
    assert first["href"] == "/job/101"
    assert first["location"] == "Guelph, ON"
    assert first["division"] == "CBS"
    assert first["department"] == "Biology"


def test_extract_tiles_adaptive_missing_fields_default_empty() -> None:
    sel = Selector(content=_SEARCH_HTML, adaptive=True)
    tiles = extract_tiles_adaptive(sel)
    second = tiles[1]
    assert second["division"] == ""
    assert second["department"] == ""


def test_extract_description_adaptive() -> None:
    sel = Selector(content=_DETAIL_HTML, adaptive=True)
    desc = extract_description_adaptive(sel)
    assert "greenhouse trials" in desc


def test_extract_description_falls_back_to_bare_span() -> None:
    html = (
        '<html><body><span class="jobdescription">'
        "Bare span fallback text.</span></body></html>"
    )
    sel = Selector(content=html, adaptive=True)
    desc = extract_description_adaptive(sel)
    assert "Bare span fallback" in desc


def test_extract_description_empty_when_absent() -> None:
    html = "<html><body><p>nothing here</p></body></html>"
    sel = Selector(content=html, adaptive=True)
    assert extract_description_adaptive(sel) == ""
