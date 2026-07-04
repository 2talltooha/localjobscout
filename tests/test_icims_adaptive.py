from __future__ import annotations

import pytest

pytest.importorskip("scrapling")

from scrapling.parser import Selector  # noqa: E402

from localjobscout.scrapers.icims import (  # noqa: E402
    extract_description_adaptive,
    extract_rows_adaptive,
)

_SEARCH_HTML = """
<html><body>
<div class="row">
  <a class="iCIMS_Anchor" href="/jobs/123/lab-tech"><h3>Lab Technician</h3></a>
  <div class="description">Process samples and maintain records.</div>
</div>
<div class="row">
  <a class="iCIMS_Anchor" href="/jobs/124/ward-clerk">Ward Clerk</a>
</div>
</body></html>
"""


def test_extract_rows_adaptive_pulls_fields() -> None:
    sel = Selector(content=_SEARCH_HTML, adaptive=True)
    seen: set[str] = set()
    rows = extract_rows_adaptive(sel, seen)
    assert len(rows) == 2
    first = rows[0]
    assert first["title"] == "Lab Technician"
    assert first["href"] == "/jobs/123/lab-tech"
    assert first["snippet"] == "Process samples and maintain records."


def test_extract_rows_adaptive_dedupes_via_seen_set() -> None:
    sel = Selector(content=_SEARCH_HTML, adaptive=True)
    seen: set[str] = {"/jobs/123/lab-tech"}
    rows = extract_rows_adaptive(sel, seen)
    assert len(rows) == 1
    assert rows[0]["title"] == "Ward Clerk"


def test_extract_rows_adaptive_falls_back_to_anchor_text_without_h3() -> None:
    sel = Selector(content=_SEARCH_HTML, adaptive=True)
    seen: set[str] = set()
    rows = extract_rows_adaptive(sel, seen)
    second = rows[1]
    assert second["title"] == "Ward Clerk"
    assert second["snippet"] == ""


def test_extract_description_adaptive_prefers_job_content() -> None:
    html = (
        '<html><body><div class="iCIMS_JobContent">'
        "Full iCIMS job content body.</div></body></html>"
    )
    sel = Selector(content=html, adaptive=True)
    assert "Full iCIMS job content" in extract_description_adaptive(sel)


def test_extract_description_adaptive_falls_back_to_main() -> None:
    html = "<html><body><main>Fallback main content.</main></body></html>"
    sel = Selector(content=html, adaptive=True)
    assert "Fallback main content" in extract_description_adaptive(sel)


def test_extract_description_adaptive_empty_when_absent() -> None:
    sel = Selector(content="<html><body></body></html>", adaptive=True)
    assert extract_description_adaptive(sel) == ""
