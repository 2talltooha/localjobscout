from __future__ import annotations

from localjobscout.db import Job, make_job_id
from localjobscout.dedup import compute_job_hash, deduplicate


def _job(title: str, company: str, location: str, posted_at: str | None = None) -> Job:
    url = f"https://example.com/{title.replace(' ', '-')}-{company}"
    return Job(
        id=make_job_id("test", url),
        source="test",
        title=title,
        url=url,
        description="",
        company=company,
        location=location,
        posted_at=posted_at,
    )


# ---------------------------------------------------------------------------
# compute_job_hash
# ---------------------------------------------------------------------------


def test_hash_consistent() -> None:
    job = _job("Python Developer", "Acme Corp", "Waterloo, ON")
    assert compute_job_hash(job) == compute_job_hash(job)


def test_hash_ignores_case() -> None:
    job_lower = _job("python developer", "acme corp", "waterloo, on")
    job_upper = _job("PYTHON DEVELOPER", "ACME CORP", "WATERLOO, ON")
    assert compute_job_hash(job_lower) == compute_job_hash(job_upper)


def test_hash_ignores_punctuation() -> None:
    job_a = _job("Python Developer", "Acme, Inc.", "Waterloo, ON")
    job_b = _job("Python Developer", "Acme Inc", "Waterloo ON")
    assert compute_job_hash(job_a) == compute_job_hash(job_b)


def test_hash_ignores_extra_whitespace() -> None:
    job_a = _job("Python  Developer", "Acme Corp", "Waterloo, ON")
    job_b = _job("Python Developer", "Acme Corp", "Waterloo, ON")
    assert compute_job_hash(job_a) == compute_job_hash(job_b)


def test_hash_differs_on_different_title() -> None:
    job_a = _job("Python Developer", "Acme", "Waterloo")
    job_b = _job("Java Developer", "Acme", "Waterloo")
    assert compute_job_hash(job_a) != compute_job_hash(job_b)


def test_hash_differs_on_different_company() -> None:
    job_a = _job("Dev", "Acme", "Waterloo")
    job_b = _job("Dev", "WidgetCo", "Waterloo")
    assert compute_job_hash(job_a) != compute_job_hash(job_b)


# ---------------------------------------------------------------------------
# deduplicate
# ---------------------------------------------------------------------------


def test_deduplicate_exact_duplicates() -> None:
    job = _job("Python Dev", "Acme", "Waterloo")
    result = deduplicate([job, job])
    assert len(result) == 1


def test_deduplicate_near_duplicates_extra_whitespace() -> None:
    job_a = _job("Python  Dev", "Acme Corp", "Waterloo, ON")
    job_b = _job("Python Dev", "Acme Corp", "Waterloo, ON")
    result = deduplicate([job_a, job_b])
    assert len(result) == 1


def test_deduplicate_different_jobs_kept() -> None:
    job_a = _job("Python Dev", "Acme", "Waterloo")
    job_b = _job("Java Dev", "Acme", "Waterloo")
    result = deduplicate([job_a, job_b])
    assert len(result) == 2


def test_deduplicate_prefers_more_recent() -> None:
    job_old = _job("Python Dev", "Acme", "Waterloo", posted_at="2025-01-01")
    job_new = _job("Python Dev", "Acme", "Waterloo", posted_at="2025-11-15")
    result = deduplicate([job_old, job_new])
    assert len(result) == 1
    assert result[0].posted_at == "2025-11-15"


def test_deduplicate_empty_list() -> None:
    assert deduplicate([]) == []


def test_deduplicate_single_job() -> None:
    job = _job("Dev", "Co", "City")
    assert deduplicate([job]) == [job]


def test_deduplicate_multi_source_same_job() -> None:
    """Same job posted on LinkedIn and Indeed → only one kept."""
    url_a = "https://linkedin.com/jobs/1"
    url_b = "https://indeed.com/viewjob?jk=abc"
    job_linkedin = Job(
        id=make_job_id("linkedin", url_a),
        source="linkedin",
        title="Python Dev",
        url=url_a,
        description="",
        company="Acme Corp",
        location="Waterloo, ON",
        posted_at="2025-11-10",
    )
    job_indeed = Job(
        id=make_job_id("indeed", url_b),
        source="indeed",
        title="Python Dev",
        url=url_b,
        description="",
        company="Acme Corp",
        location="Waterloo, ON",
        posted_at="2025-11-12",
    )
    result = deduplicate([job_linkedin, job_indeed])
    assert len(result) == 1
    assert result[0].posted_at == "2025-11-12"
