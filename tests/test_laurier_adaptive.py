from __future__ import annotations

import pytest

pytest.importorskip("scrapling")

from scrapling.parser import Selector  # noqa: E402

from localjobscout.scrapers.laurier import (  # noqa: E402
    extract_description_adaptive,
    extract_rows_adaptive,
)

_ROWS_HTML = """
<html><body>
<tr class="data-row">
  <td class="colTitle"><a class="jobTitle-link" href="/job/9">Lab Assistant</a></td>
  <td class="colLocation"><span class="jobLocation">Waterloo, ON</span></td>
  <td class="colFacility"><span class="jobFacility">Biology Dept</span></td>
</tr>
<tr class="data-row">
  <td class="colTitle"><a class="jobTitle-link" href="/job/10">Registrar Clerk</a></td>
</tr>
</body></html>
"""

_DETAIL_HTML = """
<html><body>
<span itemprop="description"><span class="jobdescription">
Support the registrar's office with student records.
</span></span>
</body></html>
"""


def test_extract_rows_adaptive_pulls_fields() -> None:
    sel = Selector(content=_ROWS_HTML, adaptive=True)
    rows = extract_rows_adaptive(sel)
    assert len(rows) == 2
    first = rows[0]
    assert first["title"] == "Lab Assistant"
    assert first["href"] == "/job/9"
    assert first["location"] == "Waterloo, ON"
    assert first["facility"] == "Biology Dept"


def test_extract_rows_adaptive_missing_location_defaults_empty() -> None:
    sel = Selector(content=_ROWS_HTML, adaptive=True)
    rows = extract_rows_adaptive(sel)
    second = rows[1]
    assert second["location"] == ""
    assert second["facility"] == ""


def test_extract_description_adaptive() -> None:
    sel = Selector(content=_DETAIL_HTML, adaptive=True)
    desc = extract_description_adaptive(sel)
    assert "registrar's office" in desc


def test_extract_description_empty_when_absent() -> None:
    sel = Selector(content="<html><body><p>nothing</p></body></html>", adaptive=True)
    assert extract_description_adaptive(sel) == ""
