from __future__ import annotations

import pytest

pytest.importorskip("scrapling")

from scrapling.parser import Selector  # noqa: E402

from localjobscout.scrapers.conestoga import (  # noqa: E402
    extract_description_adaptive,
    extract_rows_adaptive,
)

_LISTING_HTML = """
<html><body>
<table class="table table-striped">
<tr class="tableheader"><th>Req</th><th>Title</th><th>Loc</th><th>Closing</th></tr>
<tr><td><a href="/job/1">REQ001</a></td><td>Lab Assistant</td>
    <td>Kitchener, ON</td><td>2026-07-01</td></tr>
<tr><td><a href="/job/2">REQ002</a></td><td>IT Support</td>
    <td>Cambridge, ON</td><td>2026-08-01</td></tr>
</table>
</body></html>
"""

_DETAIL_HTML = """
<html><body>
<div class="col-12"><h2>Overview</h2>
    <p>Assist with lab work daily, a fairly long description here.</p></div>
<div class="col-12"><h2>Requirements</h2>
    <p>CPR certified, attention to detail needed for this role.</p></div>
<div class="col-12"><h2>Short</h2><p>too short</p></div>
</body></html>
"""


def test_extract_rows_adaptive_pulls_fields() -> None:
    sel = Selector(content=_LISTING_HTML, adaptive=True)
    rows = extract_rows_adaptive(sel)
    assert len(rows) == 2
    first = rows[0]
    assert first["title"] == "Lab Assistant"
    assert first["href"] == "/job/1"
    assert first["requisition"] == "REQ001"
    assert first["location"] == "Kitchener, ON"
    assert first["closing"] == "2026-07-01"


def test_extract_rows_adaptive_skips_header_row() -> None:
    sel = Selector(content=_LISTING_HTML, adaptive=True)
    rows = extract_rows_adaptive(sel)
    titles = [r["title"] for r in rows]
    assert "Req" not in titles


def test_extract_description_adaptive_joins_long_sections() -> None:
    sel = Selector(content=_DETAIL_HTML, adaptive=True)
    desc = extract_description_adaptive(sel)
    assert "Overview Assist with lab work daily" in desc
    assert "Requirements CPR certified" in desc


def test_extract_description_adaptive_drops_short_sections() -> None:
    sel = Selector(content=_DETAIL_HTML, adaptive=True)
    desc = extract_description_adaptive(sel)
    assert "too short" not in desc


def test_extract_description_adaptive_empty_when_no_sections() -> None:
    sel = Selector(content="<html><body><p>nothing</p></body></html>", adaptive=True)
    assert extract_description_adaptive(sel) == ""
