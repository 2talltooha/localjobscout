from __future__ import annotations

from pathlib import Path

from localjobscout import db as db_module
from localjobscout.config import ScrapersConfig, Settings
from localjobscout.db import Job, make_job_id
from localjobscout.export import write_matches_md


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        match_threshold=0.22,
        db_path=tmp_path / "jobs.db",
        scrapers=ScrapersConfig(),
    )


def _job(title: str, score: float, status: str | None = None) -> Job:
    url = f"https://example.com/{title.replace(' ', '-')}"
    return Job(
        id=make_job_id("jobbank", url),
        source="jobbank",
        title=title,
        url=url,
        description="",
        company="Acme",
        location="Waterloo, ON",
        score=score,
        application_status=status,
        first_seen="2026-05-31T00:00:00+00:00",
    )


def test_writes_only_above_threshold(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db_module.init_db(settings.db_path)
    db_module.upsert_job(_job("Good Match", 0.5))
    db_module.upsert_job(_job("Below Threshold", 0.05))

    path = write_matches_md(settings)
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "Good Match" in content
    assert "Below Threshold" not in content
    assert "1 matches" in content


def test_sorted_by_score_desc(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db_module.init_db(settings.db_path)
    db_module.upsert_job(_job("Lower", 0.3))
    db_module.upsert_job(_job("Higher", 0.9))

    content = write_matches_md(settings).read_text(encoding="utf-8")
    assert content.index("Higher") < content.index("Lower")


def test_status_checkbox_rendered(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db_module.init_db(settings.db_path)
    job = _job("Applied Job", 0.5)
    db_module.upsert_job(job)
    db_module.update_application_status(job.id, "applied")

    content = write_matches_md(settings).read_text(encoding="utf-8")
    assert "[x] applied" in content


def test_empty_db_writes_header(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db_module.init_db(settings.db_path)
    content = write_matches_md(settings).read_text(encoding="utf-8")
    assert "0 matches" in content
    assert "LocalJobScout" in content


def test_pipe_in_title_escaped(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db_module.init_db(settings.db_path)
    db_module.upsert_job(_job("Lab | Research", 0.5))
    content = write_matches_md(settings).read_text(encoding="utf-8")
    assert "Lab \\| Research" in content
