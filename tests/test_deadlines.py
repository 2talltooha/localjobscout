from __future__ import annotations

from pathlib import Path

from localjobscout import db as db_module
from localjobscout.db import Job, make_job_id
from localjobscout.matching import extract_deadline

# ─── extract_deadline ────────────────────────────────────────────────────────


def test_no_deadline_returns_none() -> None:
    assert extract_deadline("Great role, apply whenever.") is None


def test_empty_returns_none() -> None:
    assert extract_deadline("") is None


def test_iso_deadline() -> None:
    assert extract_deadline("Apply by 2026-01-15 to be considered.") == "2026-01-15"


def test_deadline_keyword_iso() -> None:
    assert extract_deadline("Deadline: 2026-03-01.") == "2026-03-01"


def test_month_name_first() -> None:
    assert extract_deadline("Closing date: January 15, 2026.") == "2026-01-15"


def test_abbreviated_month() -> None:
    assert extract_deadline("Applications close on Mar 3, 2026.") == "2026-03-03"


def test_day_first_format() -> None:
    assert extract_deadline("Apply by 15 January 2026.") == "2026-01-15"


def test_slash_format_dd_mm_yyyy() -> None:
    # Canadian DD/MM/YYYY convention
    assert extract_deadline("Closing date 15/01/2026") == "2026-01-15"


def test_ordinal_suffix_stripped() -> None:
    assert extract_deadline("Apply by January 1st, 2026.") == "2026-01-01"


def test_invalid_date_returns_none() -> None:
    # Month 13 is invalid → safe_date returns None
    assert extract_deadline("Apply by 2026-13-45.") is None


def test_requires_trigger_phrase() -> None:
    # A bare date with no trigger phrase is not treated as a deadline
    assert extract_deadline("The lab opened on 2026-01-15.") is None


# ─── DB: get_jobs_with_deadlines ─────────────────────────────────────────────


def _job(title: str, deadline: str | None, status: str | None = None) -> Job:
    url = f"https://x.com/{title.replace(' ', '-')}"
    return Job(
        id=make_job_id("t", url),
        source="t",
        title=title,
        url=url,
        description="",
        score=0.5,
        deadline=deadline,
        application_status=status,
        first_seen="2026-05-31T00:00:00+00:00",
    )


def test_get_deadlines_sorted_soonest_first(tmp_path: Path) -> None:
    db_module.init_db(tmp_path / "jobs.db")
    db_module.upsert_job(_job("Later", "2026-06-01"))
    db_module.upsert_job(_job("Sooner", "2026-05-15"))
    db_module.upsert_job(_job("No deadline", None))

    jobs = db_module.get_jobs_with_deadlines()
    assert [j.title for j in jobs] == ["Sooner", "Later"]


def test_get_deadlines_filters_on_or_after(tmp_path: Path) -> None:
    db_module.init_db(tmp_path / "jobs.db")
    db_module.upsert_job(_job("Expired", "2026-01-01"))
    db_module.upsert_job(_job("Future", "2026-12-01"))

    jobs = db_module.get_jobs_with_deadlines(on_or_after="2026-06-01")
    assert [j.title for j in jobs] == ["Future"]


def test_get_deadlines_excludes_applied(tmp_path: Path) -> None:
    db_module.init_db(tmp_path / "jobs.db")
    j = _job("Applied Job", "2026-12-01")
    db_module.upsert_job(j)
    db_module.update_application_status(j.id, "applied")

    jobs = db_module.get_jobs_with_deadlines()
    assert jobs == []


def test_deadline_persists_through_db_roundtrip(tmp_path: Path) -> None:
    db_module.init_db(tmp_path / "jobs.db")
    j = _job("Has Deadline", "2026-07-04")
    db_module.upsert_job(j)
    refetched = db_module.get_job_by_id(j.id)
    assert refetched is not None
    assert refetched.deadline == "2026-07-04"
