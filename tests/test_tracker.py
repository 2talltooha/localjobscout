from __future__ import annotations

from pathlib import Path

import pytest

from localjobscout import db as db_module
from localjobscout import tracker as tracker_module
from localjobscout.config import FocusConfig, ScrapersConfig, Settings
from localjobscout.db import Job, init_db, make_job_id, upsert_job


def _settings(tmp_path: Path) -> Settings:
    """Build a minimal Settings whose resume_path is a tmp file."""
    resume = tmp_path / "resume.txt"
    resume.write_text(
        "Jane Doe\nBiology Honours student.\nFirst Aid, CPR-C, WHMIS.",
        encoding="utf-8",
    )
    return Settings(
        location="Waterloo, ON",
        match_threshold=0.17,
        scan_interval_minutes=60,
        use_semantic_matcher=False,
        resume_path=resume,
        db_path=tmp_path / "test.db",
        scrapers=ScrapersConfig(),
        focus=FocusConfig(keywords=["biology", "research", "laboratory"]),
    )


def _seed_job(*, title: str = "Research Assistant", source: str = "jobbank") -> Job:
    url = f"https://example.com/{source}/{title.lower().replace(' ', '-')}"
    job = Job(
        id=make_job_id(source, url),
        source=source,
        title=title,
        url=url,
        description="biology research laboratory work",
        company="Test Co",
        location="Waterloo, ON",
    )
    upsert_job(job)
    return job


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path: Path) -> None:
    init_db(tmp_path / "test.db")


# ---------------------------------------------------------------------------
# resolve_job
# ---------------------------------------------------------------------------


def test_resolve_job_by_full_id() -> None:
    job = _seed_job()
    resolved = tracker_module.resolve_job(job.id)
    assert resolved.id == job.id


def test_resolve_job_by_short_prefix() -> None:
    job = _seed_job()
    resolved = tracker_module.resolve_job(job.id[:8])
    assert resolved.id == job.id


def test_resolve_job_short_prefix_too_short() -> None:
    with pytest.raises(tracker_module.JobNotFoundError, match="prefix"):
        tracker_module.resolve_job("ab")


def test_resolve_job_no_match() -> None:
    _seed_job()
    with pytest.raises(tracker_module.JobNotFoundError):
        tracker_module.resolve_job("ffffffffffffffff")


def test_resolve_job_ambiguous_prefix() -> None:
    """If two jobs share a 4-char prefix, resolve_job must refuse."""
    # Seed; then poke the DB to set two ids sharing a prefix.
    job = _seed_job()
    job2 = _seed_job(title="Lab Assistant", source="adzuna")
    db_module.update_application_status(job.id, None)

    import sqlite3
    db_path = db_module._require_db()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE jobs SET id = 'aaaa1111' WHERE id = ?", (job.id,)
        )
        conn.execute(
            "UPDATE jobs SET id = 'aaaa2222' WHERE id = ?", (job2.id,)
        )
        conn.commit()

    with pytest.raises(tracker_module.JobNotFoundError):
        tracker_module.resolve_job("aaaa")


# ---------------------------------------------------------------------------
# mark_status
# ---------------------------------------------------------------------------


def test_mark_status_sets_value() -> None:
    job = _seed_job()
    updated = tracker_module.mark_status(job.id, "interested")
    assert updated.application_status == "interested"
    assert updated.applied_at is None


def test_mark_status_applied_stamps_applied_at() -> None:
    job = _seed_job()
    updated = tracker_module.mark_status(job.id, "applied")
    assert updated.application_status == "applied"
    assert updated.applied_at is not None
    assert "T" in updated.applied_at  # ISO-8601 timestamp


def test_mark_status_clear_resets_fields() -> None:
    job = _seed_job()
    tracker_module.mark_status(job.id, "applied")
    cleared = tracker_module.mark_status(job.id, None)
    assert cleared.application_status is None
    assert cleared.applied_at is None


def test_mark_status_invalid_raises() -> None:
    job = _seed_job()
    with pytest.raises(tracker_module.InvalidStatusError):
        tracker_module.mark_status(job.id, "not-a-status")


# ---------------------------------------------------------------------------
# generate_cover_letter
# ---------------------------------------------------------------------------


def test_generate_cover_letter_writes_template_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    job = _seed_job(title="Lab Technician")
    settings = _settings(tmp_path)
    out_dir = tmp_path / "applications"

    path, backend = tracker_module.generate_cover_letter(
        job.id, settings=settings, output_dir=out_dir
    )

    assert backend == "template"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "Lab Technician" in text
    assert "Test Co" in text
    assert "Jane Doe" in text  # name from the resume's first line


def test_generate_cover_letter_stamps_path_on_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    job = _seed_job()
    settings = _settings(tmp_path)
    path, _ = tracker_module.generate_cover_letter(
        job.id, settings=settings, output_dir=tmp_path / "applications"
    )
    refreshed = db_module.get_job_by_id(job.id)
    assert refreshed is not None
    assert refreshed.cover_letter_path == str(path)


# ---------------------------------------------------------------------------
# list_applications
# ---------------------------------------------------------------------------


def test_list_applications_returns_only_tracked() -> None:
    j1 = _seed_job(title="A")
    _seed_job(title="B")
    j3 = _seed_job(title="C")
    tracker_module.mark_status(j1.id, "interested")
    tracker_module.mark_status(j3.id, "applied")

    rows = tracker_module.list_applications()
    assert len(rows) == 2
    titles = {r.title for r in rows}
    assert titles == {"A", "C"}


def test_list_applications_filter_by_status() -> None:
    j1 = _seed_job(title="A")
    j2 = _seed_job(title="B")
    tracker_module.mark_status(j1.id, "interested")
    tracker_module.mark_status(j2.id, "applied")

    only_applied = tracker_module.list_applications(status="applied")
    assert [r.title for r in only_applied] == ["B"]
