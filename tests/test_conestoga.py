from __future__ import annotations

import pytest
import respx

from localjobscout.db import make_job_id
from localjobscout.scrapers.conestoga import ConestogaScraper

_HOST = "employment.conestogac.on.ca"
_ROBOTS_URL = f"https://{_HOST}/robots.txt"
_LISTING_URL = f"https://{_HOST}/"

_ROBOTS_ALLOW = "User-agent: *\nAllow: /"

_LISTING_HTML = """
<!doctype html><html><body>
<h3>Academic</h3>
<table class="table table-striped">
  <tr class="tableheader">
    <th>Requisition Number</th><th>Job Title</th>
    <th>Location</th><th>Closing</th>
  </tr>
  <tr>
    <td><a href="ViewCompetition.aspx?id=7425">26-0078</a></td>
    <td>Professor, Pre-Service Firefighter</td>
    <td>Kitchener</td>
    <td><span>Tuesday, May 19, 2026</span></td>
  </tr>
</table>
<h3>Support Staff</h3>
<table class="table table-striped">
  <tr class="tableheader">
    <th>Requisition Number</th><th>Job Title</th>
    <th>Location</th><th>Closing</th>
  </tr>
  <tr>
    <td><a href="ViewCompetition.aspx?id=7427">26-0073</a></td>
    <td>Technologist, Medical Laboratory Sciences (Contract)</td>
    <td>Kitchener</td>
    <td><span>Wednesday, May 20, 2026</span></td>
  </tr>
  <tr>
    <td><a href="ViewCompetition.aspx?id=7383">26-0006</a></td>
    <td>Part-Time Early Childhood Educator</td>
    <td>Various</td>
    <td><span>Sunday, January 3, 2027</span></td>
  </tr>
</table>
</body></html>
"""

_EMPTY_HTML = """
<!doctype html><html><body>
<table class="table table-striped">
  <tr class="tableheader"><th>Requisition Number</th><th>Job Title</th>
    <th>Location</th><th>Closing</th></tr>
</table>
</body></html>
"""


_DETAIL_HTML = """<!doctype html><html><body>
<div class="bg-light py-5"><div class="container"><div class="row">
<div class="col-12">
<h2><span>Position Summary</span></h2>
<p>Run the medical laboratory teaching program at Conestoga College.</p>
<p>Requires Master's degree and clinical experience.</p>
</div></div></div></div>
<div class="py-5"><div class="container"><div class="row">
<div class="col-12">
<h2><span>Responsibilities</span></h2>
<p>Teach medical laboratory science courses. Supervise student clinical placements.</p>
</div></div></div></div>
</body></html>"""


@pytest.mark.asyncio
async def test_happy_path_parses_all_tables() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_ALLOW)
        mock.get(_LISTING_URL).respond(200, text=_LISTING_HTML)
        for vid in (7425, 7427, 7383):
            mock.get(
                f"{_LISTING_URL}ViewCompetition.aspx?id={vid}"
            ).respond(200, text=_DETAIL_HTML)

        jobs = await ConestogaScraper().fetch("Waterloo, ON")

    assert len(jobs) == 3
    assert all(j.source == "conestoga" for j in jobs)
    assert all(j.company == "Conestoga College" for j in jobs)

    titles = {j.title for j in jobs}
    assert "Professor, Pre-Service Firefighter" in titles
    assert "Technologist, Medical Laboratory Sciences (Contract)" in titles
    assert "Part-Time Early Childhood Educator" in titles

    prof = next(j for j in jobs if j.title.startswith("Professor"))
    expected_url = f"{_LISTING_URL}ViewCompetition.aspx?id=7425"
    assert prof.url == expected_url
    assert prof.id == make_job_id("conestoga", expected_url)
    assert prof.location == "Kitchener"
    assert "Requisition: 26-0078" in prof.description
    assert "Closing: Tuesday, May 19, 2026" in prof.description
    assert "medical laboratory teaching program" in prof.description


@pytest.mark.asyncio
async def test_detail_page_failure_keeps_metadata_description() -> None:
    """If detail page returns 503, scraper falls back to metadata description."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_ALLOW)
        mock.get(_LISTING_URL).respond(200, text=_LISTING_HTML)
        # All detail pages 503 — enrichment silently skipped.
        for vid in (7425, 7427, 7383):
            mock.get(
                f"{_LISTING_URL}ViewCompetition.aspx?id={vid}"
            ).respond(503)

        jobs = await ConestogaScraper().fetch("Waterloo, ON")

    assert len(jobs) == 3
    prof = next(j for j in jobs if j.title.startswith("Professor"))
    assert "Requisition: 26-0078" in prof.description
    assert "Position Summary" not in prof.description


@pytest.mark.asyncio
async def test_empty_listing_returns_empty_list() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_ALLOW)
        mock.get(_LISTING_URL).respond(200, text=_EMPTY_HTML)

        jobs = await ConestogaScraper().fetch("Waterloo, ON")

    assert jobs == []


@pytest.mark.asyncio
async def test_network_error_returns_empty_list() -> None:
    """polite_get returning None (500/429) → fetch returns []."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_ALLOW)
        mock.get(_LISTING_URL).respond(503)

        jobs = await ConestogaScraper().fetch("Waterloo, ON")

    assert jobs == []


@pytest.mark.asyncio
async def test_malformed_row_skipped() -> None:
    """Rows missing the requisition <a> are silently dropped."""
    mixed_html = """
    <!doctype html><html><body>
    <table class="table table-striped">
      <tr class="tableheader">
        <th>Requisition Number</th><th>Job Title</th>
        <th>Location</th><th>Closing</th>
      </tr>
      <tr>
        <td><a href="ViewCompetition.aspx?id=1">26-0001</a></td>
        <td>Valid Job</td>
        <td>Kitchener</td>
        <td><span>Soon</span></td>
      </tr>
      <tr>
        <td>NO-LINK</td>
        <td>Broken Job</td>
        <td>Kitchener</td>
        <td><span>Soon</span></td>
      </tr>
    </table>
    </body></html>
    """
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_ALLOW)
        mock.get(_LISTING_URL).respond(200, text=mixed_html)
        mock.get(f"{_LISTING_URL}ViewCompetition.aspx?id=1").respond(503)

        jobs = await ConestogaScraper().fetch("Waterloo, ON")

    assert len(jobs) == 1
    assert jobs[0].title == "Valid Job"
