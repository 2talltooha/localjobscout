from __future__ import annotations

from localjobscout.db import Job, make_job_id
from localjobscout.matching import (
    JobFilter,
    annualize_salary,
    extract_skills,
    parse_salary,
)


def _job(
    title: str = "Python Developer",
    description: str = "",
    company: str = "Acme",
    salary_min: int | None = None,
    salary_max: int | None = None,
    job_type: str = "",
    skills: list[str] | None = None,
) -> Job:
    url = f"https://example.com/{title.replace(' ', '-')}"
    return Job(
        id=make_job_id("test", url),
        source="test",
        title=title,
        url=url,
        description=description,
        company=company,
        salary_min=salary_min,
        salary_max=salary_max,
        job_type=job_type,
        skills=skills or [],
    )


# ---------------------------------------------------------------------------
# parse_salary
# ---------------------------------------------------------------------------


def test_parse_salary_annual_range() -> None:
    result = parse_salary("$50,000 - $70,000 a year")
    assert result["min"] == 50_000
    assert result["max"] == 70_000
    assert result["currency"] == "USD"
    assert result["hourly"] is False


def test_parse_salary_hourly_range() -> None:
    result = parse_salary("$25 - $30 an hour")
    assert result["min"] == 25
    assert result["max"] == 30
    assert result["hourly"] is True


def test_parse_salary_gbp_k_notation() -> None:
    result = parse_salary("£40k - £50k")
    assert result["min"] == 40_000
    assert result["max"] == 50_000
    assert result["currency"] == "GBP"


def test_parse_salary_competitive() -> None:
    result = parse_salary("Competitive")
    assert result["min"] is None
    assert result["max"] is None


def test_parse_salary_empty() -> None:
    result = parse_salary("")
    assert result["min"] is None
    assert result["max"] is None


def test_parse_salary_single_value() -> None:
    result = parse_salary("$60,000 a year")
    assert result["min"] == 60_000
    assert result["max"] == 60_000


def test_parse_salary_cad() -> None:
    result = parse_salary("CA$70,000 - CA$90,000 a year")
    assert result["min"] == 70_000
    assert result["max"] == 90_000
    assert result["currency"] == "CAD"


def test_annualize_hourly() -> None:
    salary = {"min": 25, "max": 30, "currency": "USD", "hourly": True}
    mn, mx = annualize_salary(salary)
    assert mn == 25 * 40 * 52
    assert mx == 30 * 40 * 52


def test_annualize_annual_noop() -> None:
    salary = {"min": 50_000, "max": 70_000, "currency": "USD", "hourly": False}
    mn, mx = annualize_salary(salary)
    assert mn == 50_000
    assert mx == 70_000


# ---------------------------------------------------------------------------
# extract_skills
# ---------------------------------------------------------------------------


def test_extract_skills_basic() -> None:
    skills = extract_skills("Senior Python Developer", "FastAPI and Docker experience")
    assert "Python" in skills
    assert "FastAPI" in skills
    assert "Docker" in skills


def test_extract_skills_alias_node() -> None:
    skills = extract_skills("", "nodejs developer needed")
    assert "Node.js" in skills


def test_extract_skills_case_insensitive() -> None:
    skills = extract_skills("PYTHON DEVELOPER", "")
    assert "Python" in skills


def test_extract_skills_dedup() -> None:
    skills = extract_skills("Python dev", "Python Python Python experience")
    assert skills.count("Python") == 1


def test_extract_skills_max_ten() -> None:
    desc = "Python JavaScript TypeScript Java Go Rust C++ C# Ruby PHP Swift Kotlin"
    skills = extract_skills("", desc)
    assert len(skills) <= 10


def test_extract_skills_not_present() -> None:
    skills = extract_skills("Marketing Manager", "Sales and leadership")
    assert skills == []


# ---------------------------------------------------------------------------
# JobFilter
# ---------------------------------------------------------------------------


def test_job_filter_passes_all() -> None:
    job = _job(salary_min=80_000, salary_max=100_000, skills=["Python", "AWS"])
    f = JobFilter(min_salary=70_000, required_skills=["Python"])
    assert f.matches(job) is True


def test_job_filter_salary_too_low() -> None:
    job = _job(salary_min=40_000, salary_max=50_000)
    f = JobFilter(min_salary=70_000)
    assert f.matches(job) is False


def test_job_filter_salary_too_high() -> None:
    job = _job(salary_min=150_000, salary_max=200_000)
    f = JobFilter(max_salary=120_000)
    assert f.matches(job) is False


def test_job_filter_missing_required_skill() -> None:
    job = _job(skills=["JavaScript"])
    f = JobFilter(required_skills=["Python"])
    assert f.matches(job) is False


def test_job_filter_excluded_skill_present() -> None:
    job = _job(skills=["PHP", "JavaScript"])
    f = JobFilter(excluded_skills=["PHP"])
    assert f.matches(job) is False


def test_job_filter_excluded_keyword() -> None:
    job = _job(title="Senior Manager", description="Lead the team")
    f = JobFilter(excluded_keywords=["manager"])
    assert f.matches(job) is False


def test_job_filter_allowed_job_type_match() -> None:
    job = _job(job_type="Full-time", skills=["Python"])
    f = JobFilter(allowed_job_types=["Full-time"])
    assert f.matches(job) is True


def test_job_filter_wrong_job_type() -> None:
    job = _job(job_type="Contract")
    f = JobFilter(allowed_job_types=["Full-time"])
    assert f.matches(job) is False


def test_job_filter_no_salary_passes_salary_filter() -> None:
    """Job with no salary data passes salary filter (unknown != disqualified)."""
    job = _job(salary_min=None, salary_max=None)
    f = JobFilter(min_salary=70_000)
    assert f.matches(job) is True
