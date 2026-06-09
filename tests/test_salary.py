from __future__ import annotations

from localjobscout.config import SalaryConfig
from localjobscout.db import Job, make_job_id
from localjobscout.matcher import _apply_salary_boost
from localjobscout.matching import extract_salary_from_text

# ─── extract_salary_from_text ───────────────────────────────────────────────


def test_no_salary_returns_none() -> None:
    assert extract_salary_from_text("Great entry-level role, apply now.") == (
        None, None
    )


def test_empty_text_returns_none() -> None:
    assert extract_salary_from_text("") == (None, None)


def test_annual_range() -> None:
    mn, mx = extract_salary_from_text(
        "Salary: $50,000 - $60,000 per year plus benefits."
    )
    assert mn == 50_000
    assert mx == 60_000


def test_annual_k_notation() -> None:
    mn, mx = extract_salary_from_text("Pay is $55k to $70k annually.")
    assert mn == 55_000
    assert mx == 70_000


def test_hourly_annualized() -> None:
    mn, mx = extract_salary_from_text("Wage: $20 - $25 per hour.")
    # 40h * 52w = 2080
    assert mn == 20 * 2080
    assert mx == 25 * 2080


def test_single_hourly_value() -> None:
    mn, mx = extract_salary_from_text("$18/hr to start")
    assert mn == 18 * 2080
    assert mx == 18 * 2080


def test_bare_small_number_treated_as_hourly() -> None:
    # "$25" with no period word and < 1000 → assumed hourly
    mn, mx = extract_salary_from_text("Compensation around $25.")
    assert mn == 25 * 2080


def test_ca_dollar_prefix() -> None:
    mn, mx = extract_salary_from_text("Salary CA$45,000 per year.")
    assert mn == 45_000


# ─── _apply_salary_boost ─────────────────────────────────────────────────────


def _job(salary_min: int | None = None, salary_max: int | None = None) -> Job:
    url = f"https://x.com/{salary_min}-{salary_max}"
    return Job(
        id=make_job_id("t", url),
        source="t",
        title="Lab Assistant",
        url=url,
        description="",
        salary_min=salary_min,
        salary_max=salary_max,
    )


def test_boost_disabled_by_default() -> None:
    paired = [(_job(salary_max=100_000), 0.5)]
    out = _apply_salary_boost(paired, SalaryConfig())  # enabled=False
    assert out[0][1] == 0.5


def test_boost_full_at_or_above_target() -> None:
    cfg = SalaryConfig(enabled=True, target_annual=40_000, boost=0.1)
    paired = [(_job(salary_max=50_000), 0.5)]
    out = _apply_salary_boost(paired, cfg)
    assert out[0][1] == 0.6  # full 0.1 boost


def test_boost_scales_linearly() -> None:
    cfg = SalaryConfig(enabled=True, target_annual=40_000, boost=0.1)
    paired = [(_job(salary_max=20_000), 0.5)]
    out = _apply_salary_boost(paired, cfg)
    # ratio = 20000/40000 = 0.5 → boost 0.05
    assert abs(out[0][1] - 0.55) < 1e-9


def test_boost_skips_jobs_without_salary() -> None:
    cfg = SalaryConfig(enabled=True, target_annual=40_000, boost=0.1)
    paired = [(_job(), 0.5)]
    out = _apply_salary_boost(paired, cfg)
    assert out[0][1] == 0.5


def test_boost_caps_score_at_one() -> None:
    cfg = SalaryConfig(enabled=True, target_annual=40_000, boost=0.5)
    paired = [(_job(salary_max=100_000), 0.8)]
    out = _apply_salary_boost(paired, cfg)
    assert out[0][1] == 1.0


def test_boost_uses_min_when_max_absent() -> None:
    cfg = SalaryConfig(enabled=True, target_annual=40_000, boost=0.1)
    paired = [(_job(salary_min=40_000), 0.5)]
    out = _apply_salary_boost(paired, cfg)
    assert out[0][1] == 0.6
