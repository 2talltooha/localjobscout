from __future__ import annotations

from localjobscout.db import Job, make_job_id
from localjobscout.prefilter import (
    PrefilterRules,
    description_indicates_closed,
    extract_province,
    should_exclude,
)


def _job(
    title: str = "",
    description: str = "",
    location: str = "",
    company: str = "",
) -> Job:
    url = f"https://example.com/job/{title.replace(' ', '-')}"
    return Job(
        id=make_job_id("test", url),
        source="test",
        title=title,
        url=url,
        description=description,
        location=location,
        company=company,
    )


# ---------------------------------------------------------------------------
# Phrase matching
# ---------------------------------------------------------------------------


def test_phrase_match_in_description_excludes() -> None:
    rules = PrefilterRules(exclude_phrases=["phd required"])
    job = _job(description="A great role. PhD required. Apply now.")
    excluded, reason = should_exclude(job, rules)
    assert excluded is True
    assert "phd required" in reason.lower()


def test_phrase_match_in_title_excludes() -> None:
    rules = PrefilterRules(exclude_phrases=["registered nurse required"])
    job = _job(title="Registered Nurse Required", description="Patient care role.")
    excluded, _ = should_exclude(job, rules)
    assert excluded is True


def test_phrase_no_match_passes() -> None:
    rules = PrefilterRules(exclude_phrases=["phd required"])
    job = _job(description="Python developer. No degree requirement.")
    excluded, _ = should_exclude(job, rules)
    assert excluded is False


def test_phrase_case_insensitive_by_default() -> None:
    rules = PrefilterRules(exclude_phrases=["phd required"])
    job = _job(description="PHD REQUIRED for this senior role.")
    excluded, _ = should_exclude(job, rules)
    assert excluded is True


def test_phrase_case_sensitive_mode_no_match() -> None:
    rules = PrefilterRules(
        exclude_phrases=["phd required"],
        exclude_phrase_case_sensitive=True,
    )
    job = _job(description="PHD REQUIRED for this role.")
    excluded, _ = should_exclude(job, rules)
    assert excluded is False


def test_phrase_case_sensitive_mode_exact_match() -> None:
    rules = PrefilterRules(
        exclude_phrases=["phd required"],
        exclude_phrase_case_sensitive=True,
    )
    job = _job(description="phd required for this role.")
    excluded, _ = should_exclude(job, rules)
    assert excluded is True


def test_multiple_phrases_first_match_excludes() -> None:
    rules = PrefilterRules(exclude_phrases=["must be licensed", "phd required"])
    job = _job(description="PhD required for this position.")
    excluded, _ = should_exclude(job, rules)
    assert excluded is True


# ---------------------------------------------------------------------------
# Years-experience filtering
# ---------------------------------------------------------------------------


def test_years_over_limit_excludes() -> None:
    rules = PrefilterRules(exclude_min_years_experience=2)
    job = _job(description="5+ years of Python experience required.")
    excluded, reason = should_exclude(job, rules)
    assert excluded is True
    assert "5" in reason


def test_years_under_limit_passes() -> None:
    rules = PrefilterRules(exclude_min_years_experience=3)
    job = _job(description="1+ years of experience. Junior role.")
    excluded, _ = should_exclude(job, rules)
    assert excluded is False


def test_years_exactly_at_limit_passes() -> None:
    """max_years == limit → not excluded (condition is strictly greater-than)."""
    rules = PrefilterRules(exclude_min_years_experience=2)
    job = _job(description="2 years of experience required.")
    excluded, _ = should_exclude(job, rules)
    assert excluded is False


def test_no_years_in_description_passes() -> None:
    rules = PrefilterRules(exclude_min_years_experience=2)
    job = _job(description="Python developer. Eager learner. No experience barrier.")
    excluded, _ = should_exclude(job, rules)
    assert excluded is False


def test_multiple_years_takes_max() -> None:
    """'1 year' and '5+ years' in same posting → max=5, excluded when limit=3."""
    rules = PrefilterRules(exclude_min_years_experience=3)
    job = _job(description="1 year experience in frontend. 5+ years in Python backend.")
    excluded, _ = should_exclude(job, rules)
    assert excluded is True


def test_years_limit_zero_disables_check() -> None:
    rules = PrefilterRules(exclude_min_years_experience=0)
    job = _job(description="10+ years of experience required.")
    excluded, _ = should_exclude(job, rules)
    assert excluded is False


# ---------------------------------------------------------------------------
# Empty / default rules
# ---------------------------------------------------------------------------


def test_default_rules_exclude_high_experience_job() -> None:
    """Default exclude_min_years_experience=2, so 10+ years is excluded."""
    rules = PrefilterRules()
    job = _job(title="Senior Manager", description="phd required. 10+ years.")
    excluded, _ = should_exclude(job, rules)
    assert excluded is True


def test_all_filters_disabled_never_excludes() -> None:
    """With all filters explicitly disabled, no job is excluded."""
    rules = PrefilterRules(
        exclude_phrases=[],
        exclude_min_years_experience=0,
        allowed_provinces=[],
        exclude_companies=[],
        exclude_title_regex="",
    )
    job = _job(title="Senior Manager", description="phd required. 10+ years.")
    excluded, _ = should_exclude(job, rules)
    assert excluded is False


def test_empty_description_never_excluded() -> None:
    rules = PrefilterRules(
        exclude_phrases=["phd required"],
        exclude_min_years_experience=2,
    )
    job = _job(title="Developer", description="")
    excluded, _ = should_exclude(job, rules)
    assert excluded is False


def test_reason_empty_when_not_excluded() -> None:
    rules = PrefilterRules(exclude_phrases=["phd required"])
    job = _job(description="Python developer. No PhD needed.")
    _, reason = should_exclude(job, rules)
    assert reason == ""


# ---------------------------------------------------------------------------
# Province extraction
# ---------------------------------------------------------------------------


def test_extract_province_jobbank_paren_format() -> None:
    assert extract_province("LocationFlin Flon (MB)") == "MB"


def test_extract_province_on_paren() -> None:
    assert extract_province("LocationGuelph (ON)") == "ON"


def test_extract_province_csv_format() -> None:
    assert extract_province("Waterloo, ON") == "ON"


def test_extract_province_csv_with_postal() -> None:
    assert extract_province("Guelph, ON, CA, N1G 2W1") == "ON"


def test_extract_province_full_name() -> None:
    assert extract_province("Kitchener, Ontario") == "ON"


def test_extract_province_full_name_bc() -> None:
    assert extract_province("Vancouver, British Columbia") == "BC"


def test_extract_province_bare_word_on() -> None:
    assert extract_province("Guelph ON N1G") == "ON"


def test_extract_province_none_for_ambiguous() -> None:
    # No province clues — should return None (conservative)
    assert extract_province("Waterloo region") is None


def test_extract_province_empty_string() -> None:
    assert extract_province("") is None


# ---------------------------------------------------------------------------
# Province filter
# ---------------------------------------------------------------------------


def test_province_filter_blocks_bc_job() -> None:
    rules = PrefilterRules(allowed_provinces=["ON"])
    job = _job(location="LocationFlin Flon (MB)")
    excluded, reason = should_exclude(job, rules)
    assert excluded is True
    assert "MB" in reason


def test_province_filter_blocks_ab_job() -> None:
    rules = PrefilterRules(allowed_provinces=["ON"])
    job = _job(location="LocationEdmonton (AB)")
    excluded, reason = should_exclude(job, rules)
    assert excluded is True
    assert "AB" in reason


def test_province_filter_passes_ontario_csv() -> None:
    rules = PrefilterRules(allowed_provinces=["ON"])
    job = _job(location="Waterloo, ON")
    excluded, _ = should_exclude(job, rules)
    assert excluded is False


def test_province_filter_passes_ontario_full_name() -> None:
    rules = PrefilterRules(allowed_provinces=["ON"])
    job = _job(location="Guelph, Ontario, Canada")
    excluded, _ = should_exclude(job, rules)
    assert excluded is False


def test_province_filter_passes_unknown_province() -> None:
    """Unknown province (no parseable code) should pass through conservatively."""
    rules = PrefilterRules(allowed_provinces=["ON"])
    job = _job(location="Some city somewhere")
    excluded, _ = should_exclude(job, rules)
    assert excluded is False


def test_province_filter_empty_allowed_list_passes_all() -> None:
    rules = PrefilterRules(allowed_provinces=[])
    job = _job(location="LocationFlin Flon (MB)")
    excluded, _ = should_exclude(job, rules)
    assert excluded is False


# ---------------------------------------------------------------------------
# Company blocklist
# ---------------------------------------------------------------------------


def test_company_blocklist_exact_match() -> None:
    rules = PrefilterRules(
        exclude_companies=["ApexFocusGroup"],
        allowed_provinces=[],  # disable province filter for clarity
    )
    job = _job(company="ApexFocusGroup")
    excluded, reason = should_exclude(job, rules)
    assert excluded is True
    assert "ApexFocusGroup" in reason


def test_company_blocklist_case_insensitive() -> None:
    rules = PrefilterRules(
        exclude_companies=["apexfocusgroup"],
        allowed_provinces=[],
    )
    job = _job(company="ApexFocusGroup")
    excluded, _ = should_exclude(job, rules)
    assert excluded is True


def test_company_blocklist_substring() -> None:
    rules = PrefilterRules(
        exclude_companies=["apex"],
        allowed_provinces=[],
    )
    job = _job(company="ApexFocusGroup Inc.")
    excluded, _ = should_exclude(job, rules)
    assert excluded is True


def test_company_blocklist_no_match_passes() -> None:
    rules = PrefilterRules(
        exclude_companies=["apexfocusgroup"],
        allowed_provinces=[],
    )
    job = _job(company="University of Guelph")
    excluded, _ = should_exclude(job, rules)
    assert excluded is False


# ---------------------------------------------------------------------------
# Title regex filter (CTF postings)
# ---------------------------------------------------------------------------


def test_ctf_title_regex_blocks_laurier_course_code() -> None:
    """CH250 (C): Introductory Biochemistry — should be excluded."""
    rules = PrefilterRules(
        exclude_title_regex=r"^[A-Z]{2,4}\d{3}",
        allowed_provinces=[],
    )
    job = _job(title="CH250 (C): Introductory Biochemistry")
    excluded, reason = should_exclude(job, rules)
    assert excluded is True
    assert "pattern" in reason


def test_ctf_title_regex_blocks_sy_course() -> None:
    rules = PrefilterRules(
        exclude_title_regex=r"^[A-Z]{2,4}\d{3}",
        allowed_provinces=[],
    )
    job = _job(title="SY327A, Interviews and Focus Groups (Fall 2026)")
    excluded, _ = should_exclude(job, rules)
    assert excluded is True


def test_ctf_title_regex_passes_normal_title() -> None:
    rules = PrefilterRules(
        exclude_title_regex=r"^[A-Z]{2,4}\d{3}",
        allowed_provinces=[],
    )
    job = _job(title="Laboratory Assistant - Chemistry")
    excluded, _ = should_exclude(job, rules)
    assert excluded is False


def test_invalid_regex_does_not_crash() -> None:
    rules = PrefilterRules(
        exclude_title_regex="[invalid",
        allowed_provinces=[],
    )
    job = _job(title="Some Job Title")
    excluded, _ = should_exclude(job, rules)
    assert excluded is False


# ---------------------------------------------------------------------------
# closed-posting detection
# ---------------------------------------------------------------------------


def test_description_indicates_closed_detects_banner() -> None:
    text = (
        "Great role!\n\nThis posting has been closed and applications are no "
        "longer being accepted."
    )
    assert description_indicates_closed(text) is True


def test_description_indicates_closed_case_insensitive() -> None:
    assert description_indicates_closed("APPLICATIONS ARE NOW CLOSED") is True


def test_description_indicates_closed_false_for_open_posting() -> None:
    assert description_indicates_closed("Apply now! We are hiring.") is False


def test_description_indicates_closed_none_and_empty() -> None:
    assert description_indicates_closed(None) is False
    assert description_indicates_closed("") is False


def test_description_indicates_closed_no_false_positive_on_closed_loop() -> None:
    # "closed-loop" / "no longer than" must not trip the filter.
    assert description_indicates_closed(
        "Work on closed-loop systems; tasks take no longer than a day."
    ) is False


def test_should_exclude_blocks_closed_posting() -> None:
    job = _job(
        title="Lab Assistant",
        description="This position has been filled.",
        location="Waterloo, ON",
    )
    excluded, reason = should_exclude(job, PrefilterRules())
    assert excluded is True
    assert reason == "posting closed"
