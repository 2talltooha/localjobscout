from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from localjobscout.db import Job, make_job_id
from localjobscout.report import render_html, write_report


def _mk_job(
    *,
    source: str,
    title: str,
    company: str = "Co",
    score: float | None = None,
    notified: bool = False,
    url: str | None = None,
) -> Job:
    url = url or f"https://example.com/{source}/{title.replace(' ', '-')}"
    return Job(
        id=make_job_id(source, url),
        source=source,
        title=title,
        company=company,
        location="Waterloo, ON",
        url=url,
        description="",
        first_seen="2026-05-15T10:00:00+00:00",
        score=score,
        notified=notified,
    )


def test_render_html_sorts_by_score_desc() -> None:
    job_low = _mk_job(source="jobbank", title="Low Match")
    job_high = _mk_job(source="adzuna", title="High Match")
    scored = [(job_low, 0.10), (job_high, 0.30)]

    out = render_html(
        scored, threshold=0.17, generated_at=datetime(2026, 5, 15, tzinfo=UTC)
    )

    high_idx = out.index("High Match")
    low_idx = out.index("Low Match")
    assert high_idx < low_idx, "higher-scoring job must render first"
    assert "0.300" in out and "0.100" in out


def test_render_html_escapes_user_content() -> None:
    job = _mk_job(
        source="jobbank",
        title="<script>alert(1)</script>",
        company="Co \"Quotes\"",
    )
    out = render_html([(job, 0.25)], threshold=0.17)
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out


def test_render_html_marks_above_threshold() -> None:
    job_above = _mk_job(source="adzuna", title="Above")
    job_below = _mk_job(source="jobbank", title="Below")
    out = render_html(
        [(job_above, 0.20), (job_below, 0.10)], threshold=0.17
    )
    assert out.count("✓ above") >= 1
    assert "below" in out
    assert "1 above threshold" in out or 'class="stat">' in out


def test_render_html_renders_notified_badge() -> None:
    j = _mk_job(source="jobbank", title="Notified Job", notified=True)
    out = render_html([(j, 0.25)], threshold=0.17)
    assert "notified" in out
    assert "1</strong>already notified" in out


def test_render_html_includes_all_source_chips() -> None:
    jobs = [
        (_mk_job(source="jobbank", title="A"), 0.10),
        (_mk_job(source="adzuna", title="B"), 0.20),
        (_mk_job(source="laurier", title="C"), 0.30),
    ]
    out = render_html(jobs, threshold=0.17)
    for source in ("jobbank", "adzuna", "laurier"):
        assert f'data-source-filter="{source}"' in out


def test_write_report_creates_file(tmp_path: Path) -> None:
    job = _mk_job(source="jobbank", title="Test Job")
    out_path = tmp_path / "report.html"
    result = write_report(
        [(job, 0.25)],
        threshold=0.17,
        output_path=out_path,
        open_in_browser=False,
    )
    assert result == out_path.resolve()
    assert out_path.exists()
    text = out_path.read_text(encoding="utf-8")
    assert "Test Job" in text
    assert "0.250" in text


def test_write_report_creates_parent_dirs(tmp_path: Path) -> None:
    out_path = tmp_path / "nested" / "deep" / "report.html"
    job = _mk_job(source="jobbank", title="Test")
    write_report(
        [(job, 0.25)],
        threshold=0.17,
        output_path=out_path,
        open_in_browser=False,
    )
    assert out_path.exists()


def test_write_report_does_not_open_browser_when_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []

    def _fake_open(url: str) -> bool:
        called.append(url)
        return True

    import localjobscout.report as report_module
    monkeypatch.setattr(report_module.webbrowser, "open", _fake_open)

    job = _mk_job(source="jobbank", title="Test")
    write_report(
        [(job, 0.25)],
        threshold=0.17,
        output_path=tmp_path / "r.html",
        open_in_browser=False,
    )
    assert called == [], "browser must not open when open_in_browser=False"
