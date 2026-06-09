from __future__ import annotations

import pytest
import respx

from localjobscout.db import make_job_id
from localjobscout.scrapers.laurier import LaurierScraper

_HOST = "careers.wlu.ca"
_ROBOTS_URL = f"https://{_HOST}/robots.txt"
_STAFF_P0 = f"https://{_HOST}/go/Staff-and-Management-Positions/505147/?startrow=0"
_STAFF_P1 = f"https://{_HOST}/go/Staff-and-Management-Positions/505147/?startrow=25"
_ACA_P0 = f"https://{_HOST}/go/Academic-Positions/505047/?startrow=0"
_ACA_P1 = f"https://{_HOST}/go/Academic-Positions/505047/?startrow=25"

_JOB_DETAIL_LAB = (
    f"https://{_HOST}/job/Brantford-Lab-Coordinator-Psychology-ON/111/"
)
_JOB_DETAIL_MARKETING = (
    f"https://{_HOST}/job/Waterloo-Content-Marketing-Coordinator-ON/222/"
)
_JOB_DETAIL_PROFESSOR = (
    f"https://{_HOST}/job/Waterloo-Assistant-Professor-Biology-ON/333/"
)

_ROBOTS_ALLOW = "User-agent: *\nAllow: /"
_ROBOTS_BLOCK = "User-agent: *\nDisallow: /go/"

_STAFF_HTML = """
<!doctype html><html><body>
<table>
  <tr class="data-row">
    <td class="colTitle" headers="hdrTitle">
      <span class="jobTitle hidden-phone">
        <a class="jobTitle-link"
           href="/job/Brantford-Lab-Coordinator-Psychology-ON/111/">
          Lab Coordinator, Psychology
        </a>
      </span>
    </td>
    <td class="colFacility hidden-phone">
      <span class="jobFacility">Psychology Department</span>
    </td>
    <td class="colLocation hidden-phone">
      <span class="jobLocation">Brantford, CA</span>
    </td>
  </tr>
  <tr class="data-row">
    <td class="colTitle" headers="hdrTitle">
      <span class="jobTitle hidden-phone">
        <a class="jobTitle-link"
           href="/job/Waterloo-Content-Marketing-Coordinator-ON/222/">
          Content Marketing Coordinator
        </a>
      </span>
    </td>
    <td class="colFacility hidden-phone">
      <span class="jobFacility">Communications</span>
    </td>
    <td class="colLocation hidden-phone">
      <span class="jobLocation">Waterloo, CA</span>
    </td>
  </tr>
</table>
</body></html>
"""

_ACA_HTML = """
<!doctype html><html><body>
<table>
  <tr class="data-row">
    <td class="colTitle" headers="hdrTitle">
      <span class="jobTitle hidden-phone">
        <a class="jobTitle-link"
           href="/job/Waterloo-Assistant-Professor-Biology-ON/333/">
          Assistant Professor, Biology
        </a>
      </span>
    </td>
    <td class="colFacility hidden-phone">
      <span class="jobFacility">Faculty of Science</span>
    </td>
    <td class="colLocation hidden-phone">
      <span class="jobLocation">Waterloo, CA</span>
    </td>
  </tr>
</table>
</body></html>
"""

_EMPTY_HTML = "<!doctype html><html><body><table></table></body></html>"

_DETAIL_HTML_LAB = """<!doctype html><html><body>
<span itemprop="description"><span class="jobdescription"><p>Run psychology
research lab supporting faculty and graduate students.</p></span></span>
</body></html>"""

_DETAIL_HTML_MARKETING = """<!doctype html><html><body>
<span class="jobdescription"><p>Plan and execute multi-channel marketing
campaigns.</p></span>
</body></html>"""

_DETAIL_HTML_PROFESSOR = """<!doctype html><html><body>
<span class="jobdescription"><p>Tenure-track position in molecular
biology.</p></span>
</body></html>"""


@pytest.mark.asyncio
async def test_happy_path_both_categories() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_ALLOW)
        mock.get(_STAFF_P0).respond(200, text=_STAFF_HTML)
        mock.get(_STAFF_P1).respond(200, text=_EMPTY_HTML)
        mock.get(_ACA_P0).respond(200, text=_ACA_HTML)
        mock.get(_ACA_P1).respond(200, text=_EMPTY_HTML)
        mock.get(_JOB_DETAIL_LAB).respond(200, text=_DETAIL_HTML_LAB)
        mock.get(_JOB_DETAIL_MARKETING).respond(
            200, text=_DETAIL_HTML_MARKETING
        )
        mock.get(_JOB_DETAIL_PROFESSOR).respond(
            200, text=_DETAIL_HTML_PROFESSOR
        )

        jobs = await LaurierScraper(max_pages=2).fetch("Waterloo, ON")

    assert len(jobs) == 3
    assert all(j.source == "laurier" for j in jobs)
    assert all(j.company == "Wilfrid Laurier University" for j in jobs)

    lab = next(j for j in jobs if j.title == "Lab Coordinator, Psychology")
    assert lab.location == "Brantford, CA"
    assert "psychology research lab" in lab.description
    assert lab.url == _JOB_DETAIL_LAB
    assert lab.id == make_job_id("laurier", _JOB_DETAIL_LAB)

    prof = next(j for j in jobs if j.title == "Assistant Professor, Biology")
    assert prof.location == "Waterloo, CA"
    assert "molecular" in prof.description


@pytest.mark.asyncio
async def test_empty_results_returns_empty_list() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_ALLOW)
        mock.get(_STAFF_P0).respond(200, text=_EMPTY_HTML)
        mock.get(_ACA_P0).respond(200, text=_EMPTY_HTML)

        jobs = await LaurierScraper(max_pages=2).fetch("Waterloo, ON")

    assert jobs == []


@pytest.mark.asyncio
async def test_network_error_returns_empty_list() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_ALLOW)
        mock.get(_STAFF_P0).respond(503)
        mock.get(_ACA_P0).respond(503)

        jobs = await LaurierScraper(max_pages=2).fetch("Waterloo, ON")

    assert jobs == []


@pytest.mark.asyncio
async def test_robots_blocked_returns_empty_list() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_BLOCK)
        staff_route = mock.get(_STAFF_P0).respond(200, text=_STAFF_HTML)
        aca_route = mock.get(_ACA_P0).respond(200, text=_ACA_HTML)

        jobs = await LaurierScraper(max_pages=2).fetch("Waterloo, ON")

    assert jobs == []
    assert staff_route.call_count == 0
    assert aca_route.call_count == 0


@pytest.mark.asyncio
async def test_malformed_row_skipped() -> None:
    bad_html = """<!doctype html><html><body>
    <table>
      <tr class="data-row">
        <td class="colTitle"><span class="jobTitle hidden-phone"></span></td>
      </tr>
      <tr class="data-row">
        <td class="colTitle">
          <span class="jobTitle hidden-phone">
            <a class="jobTitle-link"
               href="/job/Brantford-Lab-Coordinator-Psychology-ON/111/">
              Lab Coordinator, Psychology
            </a>
          </span>
        </td>
        <td class="colLocation hidden-phone">
          <span class="jobLocation">Brantford, CA</span>
        </td>
      </tr>
    </table>
    </body></html>"""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_ALLOW)
        mock.get(_STAFF_P0).respond(200, text=bad_html)
        mock.get(_STAFF_P1).respond(200, text=_EMPTY_HTML)
        mock.get(_ACA_P0).respond(200, text=_EMPTY_HTML)
        mock.get(_JOB_DETAIL_LAB).respond(200, text=_DETAIL_HTML_LAB)

        jobs = await LaurierScraper(max_pages=2).fetch("Waterloo, ON")

    assert len(jobs) == 1
    assert jobs[0].title == "Lab Coordinator, Psychology"


@pytest.mark.asyncio
async def test_duplicate_url_skipped() -> None:
    """Same job repeated on consecutive pages counted once."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(_ROBOTS_URL).respond(200, text=_ROBOTS_ALLOW)
        mock.get(_STAFF_P0).respond(200, text=_STAFF_HTML)
        mock.get(_STAFF_P1).respond(200, text=_STAFF_HTML)
        mock.get(_ACA_P0).respond(200, text=_EMPTY_HTML)
        mock.get(_JOB_DETAIL_LAB).respond(200, text=_DETAIL_HTML_LAB)
        mock.get(_JOB_DETAIL_MARKETING).respond(
            200, text=_DETAIL_HTML_MARKETING
        )

        jobs = await LaurierScraper(max_pages=2).fetch("Waterloo, ON")

    assert len(jobs) == 2
