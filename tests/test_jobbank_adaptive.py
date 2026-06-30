from __future__ import annotations

from pathlib import Path

import pytest

# Adaptive selectors require Scrapling; skip the whole module when absent.
pytest.importorskip("scrapling")

from scrapling.parser import Selector  # noqa: E402

from localjobscout.scrapers.jobbank import (  # noqa: E402
    extract_cards_adaptive,
    extract_description_adaptive,
)

_SEARCH_HTML = """
<html><body>
<article><a class="resultJobItem" href="/job/1;jsessionid=ABC">
  <span class="noctitle">Research Assistant</span>
  <span class="business">Acme Lab</span>
  <span class="location">Waterloo, ON</span>
</a></article>
<article><a class="resultJobItem" href="/job/2">
  <span class="noctitle">Lab Technician</span>
  <span class="business">BioCorp</span>
  <span class="location">Guelph, ON</span>
</a></article>
</body></html>
"""

_DETAIL_HTML = """
<html><body>
<div id="jobDescriptionId">Assist with sample prep and data entry in the lab.</div>
<time datetime="2026-06-01">June 1</time>
</body></html>
"""


def test_extract_cards_adaptive_pulls_fields() -> None:
    sel = Selector(content=_SEARCH_HTML, adaptive=True)
    cards = extract_cards_adaptive(sel)
    assert len(cards) == 2
    first = cards[0]
    assert first["title"] == "Research Assistant"
    assert first["company"] == "Acme Lab"
    assert first["location"] == "Waterloo, ON"
    assert first["href"] == "/job/1;jsessionid=ABC"


def test_extract_description_adaptive() -> None:
    sel = Selector(content=_DETAIL_HTML, adaptive=True)
    desc, posted = extract_description_adaptive(sel)
    assert "sample prep" in desc
    assert posted == "2026-06-01"


def test_description_falls_back_to_main_paragraphs() -> None:
    """When neither description block is present, fall back to <main> <p>s —
    parity with the legacy _fetch_detail path."""
    html = (
        "<html><body><main>"
        "<p>Assist researchers with daily lab tasks.</p>"
        "<p>CPR certification an asset.</p>"
        "</main></body></html>"
    )
    sel = Selector(content=html, adaptive=True)
    desc, _ = extract_description_adaptive(sel)
    assert "daily lab tasks" in desc
    assert "CPR certification" in desc


def test_card_selector_self_heals_after_layout_change(tmp_path: Path) -> None:
    """The brittle card-list selector relocates by fingerprint when JobBank
    renames the class — the whole point of Phase 2."""
    store = {"storage_file": str(tmp_path / "adaptive.db")}
    css = "article a.resultJobItem"
    ident = "jobbank_card_list"

    # Run 1: original markup — fingerprint the cards.
    s1 = Selector(content=_SEARCH_HTML, adaptive=True, storage_args=store)
    saved = s1.css(css, identifier=ident, adaptive=True, auto_save=True)
    assert len(saved) == 2

    # Layout change: JobBank renames the class → the fixed selector breaks.
    mutated = _SEARCH_HTML.replace("resultJobItem", "jobCardLink")
    s2 = Selector(content=mutated, adaptive=True, storage_args=store)

    # Fixed selector now matches nothing…
    assert len(s2.css(css)) == 0
    # …but adaptive relocates the saved element by similarity.
    healed = s2.css(css, identifier=ident, adaptive=True, auto_save=False)
    assert len(healed) >= 1
    assert healed[0].attrib.get("href") == "/job/1;jsessionid=ABC"
