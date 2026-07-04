from __future__ import annotations

from pathlib import Path

import pytest

from localjobscout.db import (
    Job,
    get_all_job_hashes,
    get_all_job_ids,
    get_cowork_candidates,
    get_job_by_id,
    get_manual_queue_jobs,
    get_recent_jobs,
    get_unnotified_above,
    init_db,
    make_job_id,
    mark_notified,
    set_suitability,
    update_score,
    update_scores,
    upsert_job,
    upsert_jobs,
)


def _job(title: str = "Software Engineer", score: float | None = None) -> Job:
    url = f"https://example.com/jobs/{title.lower().replace(' ', '-')}"
    return Job(
        id=make_job_id("test", url),
        source="test",
        title=title,
        url=url,
        description=f"A great role for a {title} in Waterloo.",
        company="Acme Corp",
        location="Waterloo, ON",
        score=score,
    )


@pytest.fixture(autouse=True)
def fresh_db(tmp_path: Path) -> None:
    """Point the module at a fresh per-test database before each test."""
    init_db(tmp_path / "test.db")


# ---------------------------------------------------------------------------
# upsert_job
# ---------------------------------------------------------------------------


def test_new_job_returns_true() -> None:
    assert upsert_job(_job()) is True


def test_duplicate_job_returns_false() -> None:
    job = _job()
    upsert_job(job)
    assert upsert_job(job) is False


def test_upsert_refreshes_description_when_longer() -> None:
    """A reseen job with a fuller description (e.g. enrichment/adaptive fetch
    superseding a truncated snippet) updates the stored description in place."""
    job = _job()
    job.description = "short snippet"
    upsert_job(job)

    job.description = "A much longer, fully enriched job description body."
    assert upsert_job(job) is False  # still a duplicate insert
    stored = get_job_by_id(job.id)
    assert stored is not None
    assert stored.description == "A much longer, fully enriched job description body."


def test_upsert_keeps_description_when_shorter() -> None:
    """A reseen job with a shorter description never regresses the stored one."""
    job = _job()
    job.description = "A much longer, fully enriched job description body."
    upsert_job(job)

    job.description = "short snippet"
    upsert_job(job)
    stored = get_job_by_id(job.id)
    assert stored is not None
    assert stored.description == "A much longer, fully enriched job description body."


def test_upsert_idempotent_no_duplicate_rows() -> None:
    job = _job("Backend Dev", score=0.9)
    upsert_job(job)
    upsert_job(job)
    results = get_unnotified_above(0.0)
    assert len(results) == 1


# ---------------------------------------------------------------------------
# mark_notified / get_unnotified_above
# ---------------------------------------------------------------------------


def test_notified_job_excluded_from_filter() -> None:
    job = _job(score=0.8)
    upsert_job(job)
    mark_notified(job.id)
    assert all(r.id != job.id for r in get_unnotified_above(0.0))


def test_unnotified_job_appears_in_filter() -> None:
    job = _job(score=0.8)
    upsert_job(job)
    assert any(r.id == job.id for r in get_unnotified_above(0.0))


def test_threshold_filters_low_scores() -> None:
    low = _job("Junior Role", score=0.1)
    high = _job("Senior Role", score=0.9)
    upsert_job(low)
    upsert_job(high)
    results = get_unnotified_above(0.5)
    ids = {r.id for r in results}
    assert high.id in ids
    assert low.id not in ids


def test_null_score_excluded_from_filter() -> None:
    """SQL WHERE score >= ? skips NULL scores — they should never surface."""
    job = _job("Unscored Job", score=None)
    upsert_job(job)
    assert all(r.id != job.id for r in get_unnotified_above(0.0))


def test_empty_db_returns_empty_list() -> None:
    assert get_unnotified_above(0.0) == []


def test_make_job_id_is_deterministic() -> None:
    a = make_job_id("jobbank", "https://example.com/job/1")
    b = make_job_id("jobbank", "https://example.com/job/1")
    assert a == b


def test_make_job_id_differs_by_source() -> None:
    a = make_job_id("jobbank", "https://example.com/job/1")
    b = make_job_id("remoteok", "https://example.com/job/1")
    assert a != b


# ---------------------------------------------------------------------------
# update_score
# ---------------------------------------------------------------------------


def test_update_score_sets_value() -> None:
    job = _job("Scored Role")
    upsert_job(job)
    update_score(job.id, 0.75)
    results = get_unnotified_above(0.5)
    assert any(r.id == job.id for r in results)


def test_update_score_nonexistent_id_does_not_raise() -> None:
    update_score("nonexistent-id-xyz", 0.9)  # must not raise


# ---------------------------------------------------------------------------
# get_recent_jobs
# ---------------------------------------------------------------------------


def test_get_recent_jobs_returns_most_recent_limit_respected(
    tmp_path: Path,
) -> None:
    init_db(tmp_path / "jobs.db")
    jobs: list[Job] = []
    for n in range(1, 6):
        url = f"https://example.com/job{n}"
        job = Job(
            id=make_job_id("test", url),
            source="test",
            title=f"Job {n}",
            url=url,
            description=f"Description for job {n}.",
            first_seen=f"2024-01-01T00:00:0{n}+00:00",
        )
        jobs.append(job)
        upsert_job(job)

    results = get_recent_jobs(limit=3)

    assert len(results) == 3
    assert results[0].id == jobs[4].id
    assert results[1].id == jobs[3].id
    assert results[2].id == jobs[2].id
    result_ids = {r.id for r in results}
    assert jobs[0].id not in result_ids
    assert jobs[1].id not in result_ids


# ---------------------------------------------------------------------------
# get_manual_queue_jobs — freshness filters (deadline + age cap)
# ---------------------------------------------------------------------------


def _queue_job(
    title: str,
    *,
    posted_at: str | None = None,
    first_seen: str = "2026-06-01T00:00:00+00:00",
    deadline: str | None = None,
) -> Job:
    url = f"https://example.com/q/{title.lower().replace(' ', '-')}"
    return Job(
        id=make_job_id("test", url),
        source="test",
        title=title,
        url=url,
        description=f"Role: {title}.",
        company="Acme",
        location="Waterloo, ON",
        score=0.5,
        posted_at=posted_at,
        first_seen=first_seen,
        deadline=deadline,
    )


def test_manual_queue_hides_expired_deadline() -> None:
    live = _queue_job("Live Role", deadline="2026-06-30")
    expired = _queue_job("Expired Role", deadline="2026-05-01")
    upsert_job(live)
    upsert_job(expired)
    ids = {j.id for j in get_manual_queue_jobs(0.0, today="2026-06-01")}
    assert live.id in ids
    assert expired.id not in ids


def test_manual_queue_keeps_jobs_without_deadline() -> None:
    job = _queue_job("No Deadline Role", deadline=None)
    upsert_job(job)
    ids = {j.id for j in get_manual_queue_jobs(0.0, today="2026-06-01")}
    assert job.id in ids


def test_manual_queue_hides_stale_by_first_seen() -> None:
    fresh = _queue_job("Fresh Role", first_seen="2026-05-25T00:00:00+00:00")
    stale = _queue_job("Stale Role", first_seen="2026-03-01T00:00:00+00:00")
    upsert_job(fresh)
    upsert_job(stale)
    ids = {j.id for j in get_manual_queue_jobs(0.0, min_date="2026-05-02")}
    assert fresh.id in ids
    assert stale.id not in ids


def test_manual_queue_prefers_posted_at_over_first_seen() -> None:
    # Scraped today (fresh first_seen) but the posting itself is old → hidden.
    old_posting = _queue_job(
        "Old Posting",
        posted_at="2026-03-15",
        first_seen="2026-06-01T00:00:00+00:00",
    )
    upsert_job(old_posting)
    ids = {j.id for j in get_manual_queue_jobs(0.0, min_date="2026-05-02")}
    assert old_posting.id not in ids


def test_manual_queue_blank_posted_at_falls_back_to_first_seen() -> None:
    # Empty-string posted_at must not be treated as a real date (NULLIF guard).
    job = _queue_job(
        "Blank Posted",
        posted_at="",
        first_seen="2026-05-25T00:00:00+00:00",
    )
    upsert_job(job)
    ids = {j.id for j in get_manual_queue_jobs(0.0, min_date="2026-05-02")}
    assert job.id in ids


# ---------------------------------------------------------------------------
# get_cowork_candidates
# ---------------------------------------------------------------------------


def test_cowork_candidates_filters_by_threshold() -> None:
    low = _job("Low Score", score=0.1)
    high = _job("High Score", score=0.5)
    upsert_job(low)
    upsert_job(high)
    ids = {j.id for j in get_cowork_candidates(0.3)}
    assert high.id in ids
    assert low.id not in ids


def test_cowork_candidates_excludes_gate_blocked_by_default() -> None:
    ok = _job("Qualified", score=0.5)
    blocked = _job("Gate Blocked", score=0.5)
    upsert_job(ok)
    upsert_job(blocked)
    set_suitability(blocked.id, 0.5, "reason", verdict="no")
    ids = {j.id for j in get_cowork_candidates(0.3, gate_qualified=True)}
    assert ok.id in ids
    assert blocked.id not in ids


def test_cowork_candidates_gate_qualified_false_includes_all() -> None:
    ok = _job("Qualified", score=0.5)
    blocked = _job("Gate Blocked", score=0.5)
    upsert_job(ok)
    upsert_job(blocked)
    set_suitability(blocked.id, 0.5, "reason", verdict="no")
    ids = {j.id for j in get_cowork_candidates(0.3, gate_qualified=False)}
    assert ok.id in ids
    assert blocked.id in ids


def test_cowork_candidates_unscored_verdict_included() -> None:
    job = _job("No Verdict Yet", score=0.5)
    upsert_job(job)
    ids = {j.id for j in get_cowork_candidates(0.3, gate_qualified=True)}
    assert job.id in ids


# ---------------------------------------------------------------------------
# Batch helpers: upsert_jobs, get_all_job_hashes, get_all_job_ids, update_scores
# ---------------------------------------------------------------------------


def test_upsert_jobs_batch_returns_per_job_is_new_flags() -> None:
    a = _job("Job A")
    b = _job("Job B")
    flags = upsert_jobs([a, b])
    assert flags == [True, True]

    flags2 = upsert_jobs([a, b])
    assert flags2 == [False, False]


def test_upsert_jobs_empty_list_returns_empty() -> None:
    assert upsert_jobs([]) == []


def test_get_all_job_hashes_reflects_stored_jobs() -> None:
    job = _job("Hash Job")
    job.job_hash = "abc123"
    upsert_job(job)
    assert "abc123" in get_all_job_hashes()


def test_get_all_job_hashes_excludes_blank() -> None:
    job = _job("No Hash Job")
    job.job_hash = ""
    upsert_job(job)
    assert "" not in get_all_job_hashes()


def test_get_all_job_ids_reflects_stored_jobs() -> None:
    job = _job("Id Job")
    upsert_job(job)
    assert job.id in get_all_job_ids()


def test_get_all_job_ids_empty_db() -> None:
    assert get_all_job_ids() == set()


def test_update_scores_batch_sets_values() -> None:
    a = _job("Batch A")
    b = _job("Batch B")
    upsert_job(a)
    upsert_job(b)
    update_scores([(a.id, 0.6), (b.id, 0.9)])
    results = {r.id: r.score for r in get_unnotified_above(0.0)}
    assert results[a.id] == 0.6
    assert results[b.id] == 0.9


def test_update_scores_empty_list_no_op() -> None:
    update_scores([])  # must not raise
