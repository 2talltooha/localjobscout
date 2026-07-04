from __future__ import annotations

import pytest

pytest.importorskip("scrapling")

from scrapling.parser import Selector  # noqa: E402

from localjobscout.scrapers.talent import (  # noqa: E402
    extract_cards_adaptive,
    extract_description_adaptive,
)

_SEARCH_HTML = """
<html><body>
<article data-testid="job-card-unified">
  <a href="/view?id=99">link</a>
  <div class="JobCard_title__abc">Pharmacy Technician</div>
  <div class="JobCard_company__abc">HealthCo</div>
  <div class="JobCard_location__abc">Kitchener, ON</div>
  <div class="JobCard_description__abc">Assist pharmacist with daily tasks.</div>
</article>
<article data-testid="job-card-unified">
  <a href="/view?id=100">link</a>
  <div class="JobCard_title__abc">Lab Tech</div>
</article>
</body></html>
"""


def test_extract_cards_adaptive_pulls_fields() -> None:
    sel = Selector(content=_SEARCH_HTML, adaptive=True)
    seen: set[str] = set()
    cards = extract_cards_adaptive(sel, seen)
    assert len(cards) == 2
    first = cards[0]
    assert first["title"] == "Pharmacy Technician"
    assert first["company"] == "HealthCo"
    assert first["location"] == "Kitchener, ON"
    assert first["snippet"] == "Assist pharmacist with daily tasks."
    assert first["href"] == "https://ca.talent.com/view?id=99"


def test_extract_cards_adaptive_dedupes_via_seen_set() -> None:
    sel = Selector(content=_SEARCH_HTML, adaptive=True)
    seen: set[str] = {"https://ca.talent.com/view?id=99"}
    cards = extract_cards_adaptive(sel, seen)
    assert len(cards) == 1
    assert cards[0]["title"] == "Lab Tech"


def test_extract_cards_adaptive_defaults_missing_company() -> None:
    sel = Selector(content=_SEARCH_HTML, adaptive=True)
    seen: set[str] = set()
    cards = extract_cards_adaptive(sel, seen)
    second = cards[1]
    assert second["company"] == "Unknown"


def test_extract_description_adaptive_prefers_first_candidate() -> None:
    html = (
        '<html><body><div class="jobDescriptionBody">'
        "Full posting body text.</div></body></html>"
    )
    sel = Selector(content=html, adaptive=True)
    assert "Full posting body" in extract_description_adaptive(sel)


def test_extract_description_adaptive_falls_back_to_main() -> None:
    html = "<html><body><main>Fallback main content.</main></body></html>"
    sel = Selector(content=html, adaptive=True)
    assert "Fallback main content" in extract_description_adaptive(sel)


def test_extract_description_adaptive_empty_when_absent() -> None:
    sel = Selector(content="<html><body></body></html>", adaptive=True)
    assert extract_description_adaptive(sel) == ""
