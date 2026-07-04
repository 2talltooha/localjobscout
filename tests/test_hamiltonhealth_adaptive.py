from __future__ import annotations

import pytest

pytest.importorskip("scrapling")

from scrapling.parser import Selector  # noqa: E402

from localjobscout.scrapers.hamiltonhealth import (  # noqa: E402
    extract_description_adaptive,
    extract_rows_adaptive,
)

_ROWS_HTML = """
<html><body>
<tr class="data-row">
  <td><a class="jobTitle-link" href="/job/55">RN - Med Surg</a></td>
  <td><span class="jobLocation">Hamilton, ON</span></td>
  <td><span class="jobFacility">Hamilton General</span></td>
</tr>
<tr class="data-row">
  <td><a class="jobTitle-link" href="/job/56">Ward Clerk</a></td>
</tr>
</body></html>
"""

_DETAIL_HTML = """
<html><body>
<span itemprop="description"><span class="jobdescription">
Provide nursing care on the med-surg unit.
</span></span>
</body></html>
"""


def test_extract_rows_adaptive_pulls_fields() -> None:
    sel = Selector(content=_ROWS_HTML, adaptive=True)
    rows = extract_rows_adaptive(sel)
    assert len(rows) == 2
    first = rows[0]
    assert first["title"] == "RN - Med Surg"
    assert first["href"] == "/job/55"
    assert first["location"] == "Hamilton, ON"
    assert first["facility"] == "Hamilton General"


def test_extract_rows_adaptive_missing_location_defaults_hamilton() -> None:
    sel = Selector(content=_ROWS_HTML, adaptive=True)
    rows = extract_rows_adaptive(sel)
    second = rows[1]
    assert second["location"] == "Hamilton, ON"
    assert second["facility"] == ""


def test_extract_description_adaptive() -> None:
    sel = Selector(content=_DETAIL_HTML, adaptive=True)
    desc = extract_description_adaptive(sel)
    assert "med-surg unit" in desc


def test_extract_description_empty_when_absent() -> None:
    sel = Selector(content="<html><body><p>nothing</p></body></html>", adaptive=True)
    assert extract_description_adaptive(sel) == ""
