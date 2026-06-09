from __future__ import annotations

import pytest
import respx

from localjobscout.db import make_job_id
from localjobscout.scrapers.uoguelph import UofGScraper

_HOST = "careers.uoguelph.ca"
_ROBOTS_URL = f"https://{_HOST}/robots.txt"
_SEARCH_P0 = f"https://{_HOST}/search/?startrow=0"
_SEARCH_P1 = f"https://{_HOST}/search/?startrow=20"

_JOB_DETAIL_RA = f"https://{_HOST}/job/Guelph-Research-Assistant-ON/111/"
_JOB_DETAIL_LAB = f"https://{_HOST}/job/Guelph-Lab-Technician-ON/222/"

_ROBOTS_ALLOW = "User-agent: *\nAllow: /"
_ROBOTS_BLOCK = "User-agent: *\nDisallow: /search/"

_LISTING_HTML = """
<!doctype html><html><body>
<ul id="job-tile-list">
  <li class="job-tile job-id-111">
    <div class="col-md-12 sub-section sub-section-desktop">
      <div class="tiletitle">
        <a class="jobTitle-link" href="/job/Guelph-Research-Assistant-ON/111/">
          Research Assistant
        </a>
      </div>
      <div class="section-field location">
        <span class="section-label">Location</span>
        <div>Guelph, ON, CA, N1G 2W1</div>
      </div>
      <div class="section-field facility">
        <span class="section-label">Division</span>
        <div>Biology Department</div>
      </div>
      <div class="section-field dept">
        <span class="section-label">Department</span>
        <div>Plant Sciences</div>
      </div>
    </div>
  </li>
  <li class="job-tile job-id-222">
    <div class="col-md-12 sub-section sub-section-desktop">
      <div class="tiletitle">
        <a class="jobTitle-link" href="/job/Guelph-Lab-Technician-ON/222/">
          Lab Technician
        </a>
      </div>
      <div class="section-field location">
        <span class="section-label">Location</span>
        <div>Guelph, ON, CA</div>
      </div>
    </div>
  </li>
</ul>
</body></html>
"""

_EMPTY_HTML = "<!doctype html><html><body><ul id='job-tile-list'></ul></body></html>"

_DETAIL_HTML_RA = """<!doctype html><html><body>
<span itemprop="description"><span class="jobdescription"><p>PCR and cell
culture bench work for plant biology research.</p></span></span>
</body></html>"""

_DETAIL_HTML_LAB = """<!doctype html><html><body>
<span class="jobdescription"><p>Lab glassware washing and basic chemistry
prep.</p></span>
</body></html>"""


@pytest.mark.asyncio
async def test_happy_path_single_page() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_ALLOW)
        mock.get(_SEARCH_P0).respond(200, text=_LISTING_HTML)
        mock.get(_SEARCH_P1).respond(200, text=_EMPTY_HTML)
        mock.get(_JOB_DETAIL_RA).respond(200, text=_DETAIL_HTML_RA)
        mock.get(_JOB_DETAIL_LAB).respond(200, text=_DETAIL_HTML_LAB)

        jobs = await UofGScraper(max_pages=2).fetch("Waterloo, ON")

    assert len(jobs) == 2
    assert all(j.source == "uoguelph" for j in jobs)
    assert all(j.company == "University of Guelph" for j in jobs)

    research = next(j for j in jobs if j.title == "Research Assistant")
    assert research.location == "Guelph, ON, CA, N1G 2W1"
    assert "PCR" in research.description
    assert "plant biology" in research.description
    expected_url = _JOB_DETAIL_RA
    assert research.url == expected_url
    assert research.id == make_job_id("uoguelph", expected_url)

    lab = next(j for j in jobs if j.title == "Lab Technician")
    assert lab.location == "Guelph, ON, CA"
    assert "Lab glassware" in lab.description
    assert "Division" not in lab.description


@pytest.mark.asyncio
async def test_empty_results_returns_empty_list() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_ALLOW)
        first_page = mock.get(_SEARCH_P0).respond(200, text=_EMPTY_HTML)
        second_page = mock.get(_SEARCH_P1).respond(200, text=_LISTING_HTML)

        jobs = await UofGScraper(max_pages=3).fetch("Waterloo, ON")

    assert jobs == []
    assert first_page.call_count == 1
    # Empty first page should short-circuit; we never hit the second.
    assert second_page.call_count == 0


@pytest.mark.asyncio
async def test_network_error_returns_empty_list() -> None:
    """polite_get returning None (500/429) → fetch returns []."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_ALLOW)
        mock.get(_SEARCH_P0).respond(500)

        jobs = await UofGScraper(max_pages=2).fetch("Waterloo, ON")

    assert jobs == []


@pytest.mark.asyncio
async def test_robots_blocked_returns_empty_list() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_BLOCK)
        search_route = mock.get(_SEARCH_P0).respond(200, text=_LISTING_HTML)

        jobs = await UofGScraper(max_pages=2).fetch("Waterloo, ON")

    assert jobs == []
    assert search_route.call_count == 0


@pytest.mark.asyncio
async def test_duplicate_url_skipped() -> None:
    """Same job appearing on consecutive pages is only counted once."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_ALLOW)
        mock.get(_SEARCH_P0).respond(200, text=_LISTING_HTML)
        mock.get(_SEARCH_P1).respond(200, text=_LISTING_HTML)
        mock.get(_JOB_DETAIL_RA).respond(200, text=_DETAIL_HTML_RA)
        mock.get(_JOB_DETAIL_LAB).respond(200, text=_DETAIL_HTML_LAB)

        jobs = await UofGScraper(max_pages=2).fetch("Waterloo, ON")

    # 2 unique URLs even though we fetched the same page twice.
    assert len(jobs) == 2
