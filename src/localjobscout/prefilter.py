from __future__ import annotations

import re

from pydantic import BaseModel

from localjobscout.db import Job

# Matches "5 years", "5+ years", and parenthesised counts like "three (3) years".
_YEARS_RE = re.compile(r"(\d+)\+?\)?\s*(?:years?|yrs?)", re.IGNORECASE)

# Required completed post-secondary credential a first-year student lacks, e.g.
# "minimum of three (3) years of Community College education", "2 years of
# university studies". Education years — separate from work experience.
_EDU_YEARS_RE = re.compile(
    r"\(?\d+\)?\s*years?\s+(?:of\s+)?[\w\s,/]{0,40}?"
    r"(?:college|university|post-?secondary|undergraduate)\s+"
    r"(?:education|degree|studies|diploma)",
    re.IGNORECASE,
)

# A completed post-secondary credential or professional registration the
# applicant lacks: "must have a degree", "college diploma required",
# "eligibility for CALAS registration".
# Deliberately does NOT match a bare "high school diploma" (he has one) or
# "no degree required" / "degree an asset" (not hard requirements).
_CREDENTIAL_REQ_RE = re.compile(
    r"(?:must (?:have|possess|hold)|requires?|required[:\s]|minimum of|completion of)"
    r"[\w\s,'/-]{0,40}?(?:designation"
    r"|(?:college|university|post-?secondary)\s+diploma)"
    r"|(?<!no )(?:degree|designation)\s+(?:is\s+)?(?:required|mandatory)"
    r"|eligibility for\s+[\w\s]{0,30}?(?:registration|certification|licen[cs]e)",
    re.IGNORECASE,
)

# A specific named post-secondary degree (bachelor/master/doctoral/medical/PhD).
# \S{0,4} tolerates "'s", "s", and mojibake apostrophes ("Bachelor's degree").
_DEGREE_TYPE_RE = re.compile(
    r"(?:bachelor|master|doctora|ph\.?\s?d|medical|mbbs|m\.?d\.?)\S{0,4}\s+degree",
    re.IGNORECASE,
)
# Student phrasing — a degree mentioned this way is the applicant's path, not a
# hard requirement, so it must NOT trigger exclusion.
_STUDENT_CTX_RE = re.compile(
    r"(?:pursuing|toward|towards|working on|working toward|enrolled|"
    r"currently completing|studying|in progress|in pursuit of)",
    re.IGNORECASE,
)


def credential_block(text: str | None) -> str | None:
    """Return a reason string if the posting demands a completed post-secondary
    credential / professional registration the first-year applicant lacks, else
    None. Shared by the scan-time prefilter and the display-time suitability
    check so both agree."""
    if not text:
        return None
    if _EDU_YEARS_RE.search(text):
        return "requires completed multi-year post-secondary education"
    if _CREDENTIAL_REQ_RE.search(text):
        return "requires completed degree / professional registration"
    # Named degree (Bachelor's/Master's/Medical/PhD) unless framed as the
    # applicant's in-progress studies.
    for m in _DEGREE_TYPE_RE.finditer(text):
        pre = text[max(0, m.start() - 25) : m.start()].lower()
        if not _STUDENT_CTX_RE.search(pre):
            return "requires a completed degree"
    return None

# Map of lowercase full province names to 2-letter codes
_FULL_PROVINCE: dict[str, str] = {
    "ontario": "ON",
    "british columbia": "BC",
    "alberta": "AB",
    "manitoba": "MB",
    "quebec": "QC",
    "québec": "QC",
    "saskatchewan": "SK",
    "nova scotia": "NS",
    "new brunswick": "NB",
    "newfoundland": "NL",
    "prince edward island": "PE",
    "northwest territories": "NT",
    "yukon": "YT",
    "nunavut": "NU",
}

_CA_PROVINCE_CODES: frozenset[str] = frozenset(_FULL_PROVINCE.values())

# Laurier contract teaching faculty course-code pattern: "CH250 (C): ..."
_CTF_TITLE_RE = re.compile(r"^[A-Z]{2,4}\d{3}", re.IGNORECASE)

# Phrases that mean the posting is closed / no longer accepting applications.
# Matched case-insensitively as substrings of the job description.
_CLOSED_PHRASES: tuple[str, ...] = (
    "this posting has been closed",
    "posting has been closed",
    "applications are no longer being accepted",
    "no longer accepting applications",
    "we are no longer accepting applications",
    "this position has been filled",
    "this position is no longer available",
    "this job is no longer available",
    "this job posting is no longer available",
    "this competition is now closed",
    "this competition has closed",
    "posting has expired",
    "this posting has expired",
    "applications are now closed",
)


def description_indicates_closed(text: str | None) -> bool:
    """Return True if the text contains a phrase signalling a closed posting."""
    if not text:
        return False
    low = text.lower()
    return any(phrase in low for phrase in _CLOSED_PHRASES)


def extract_province(location: str) -> str | None:
    """Return the 2-letter Canadian province code from a location string.

    Returns None if no province can be determined (conservative — when unknown
    the province filter passes the job through rather than blocking it).

    Handles:
    - JobBank format: "LocationFlin Flon (MB)"
    - Standard:       "Waterloo, ON" / "Guelph, ON, CA, N1G 2W1"
    - Full name:      "Kitchener, Ontario"
    - Bare word:      "Ontario"
    """
    if not location:
        return None

    loc_lower = location.lower()

    # Full province name anywhere in the string (most specific — check first)
    for name, code in _FULL_PROVINCE.items():
        if name in loc_lower:
            return code

    # Parenthesised 2-letter code: "(AB)"
    m = re.search(r"\(([A-Z]{2})\)", location)
    if m and m.group(1) in _CA_PROVINCE_CODES:
        return m.group(1)

    # After comma: "Waterloo, ON" / "Guelph, ON, CA"
    m2 = re.search(r",\s*([A-Z]{2})\b", location)
    if m2 and m2.group(1) in _CA_PROVINCE_CODES:
        return m2.group(1)

    # Standalone word boundary: " ON " / "ON," to catch "Guelph ON N1G"
    if re.search(r"\bON\b", location):
        return "ON"

    return None


class PrefilterRules(BaseModel):
    # Existing fields
    exclude_phrases: list[str] = []
    exclude_min_years_experience: int = 2  # 0 = disabled
    exclude_phrase_case_sensitive: bool = False

    # Province filter — empty list = allow all provinces
    allowed_provinces: list[str] = ["ON"]

    # Company blocklist — case-insensitive substring match
    exclude_companies: list[str] = []

    # Title regex — if non-empty, titles matching this pattern are excluded
    exclude_title_regex: str = r"^[A-Z]{2,4}\d{3}"  # blocks Laurier CTF postings


def should_exclude(job: Job, rules: PrefilterRules) -> tuple[bool, str]:
    """Return (True, reason) if the job should be filtered out, else (False, '')."""
    text = f"{job.title} {job.description}"

    # ── Closed-posting filter ─────────────────────────────────────────────────
    if description_indicates_closed(job.description):
        return True, "posting closed"

    # ── Phrase filter ─────────────────────────────────────────────────────────
    if rules.exclude_phrases:
        haystack = text if rules.exclude_phrase_case_sensitive else text.lower()
        for phrase in rules.exclude_phrases:
            needle = phrase if rules.exclude_phrase_case_sensitive else phrase.lower()
            if needle in haystack:
                return True, f"matched phrase: '{phrase}'"

    # ── Education / credential-requirement filter ─────────────────────────────
    # Block postings demanding a completed post-secondary credential or pro
    # registration the first-year applicant does not hold.
    cred_reason = credential_block(text)
    if cred_reason:
        return True, cred_reason

    # ── Years-experience filter ───────────────────────────────────────────────
    if rules.exclude_min_years_experience > 0:
        numbers = [int(m.group(1)) for m in _YEARS_RE.finditer(text)]
        if numbers:
            max_years = max(numbers)
            if max_years > rules.exclude_min_years_experience:
                return (
                    True,
                    f"requires {max_years} years experience "
                    f"(max allowed: {rules.exclude_min_years_experience})",
                )

    # ── Province filter ───────────────────────────────────────────────────────
    if rules.allowed_provinces:
        province = extract_province(job.location or "")
        if province is not None and province not in rules.allowed_provinces:
            return True, f"out-of-province ({province})"

    # ── Company blocklist ─────────────────────────────────────────────────────
    if rules.exclude_companies:
        company_lower = (job.company or "").lower()
        for blocked in rules.exclude_companies:
            if blocked.lower() in company_lower:
                return True, f"blocked company: '{job.company}'"

    # ── Title regex filter ────────────────────────────────────────────────────
    if rules.exclude_title_regex:
        try:
            if re.search(
                rules.exclude_title_regex, job.title or "", re.IGNORECASE
            ):
                return True, "title matches exclude pattern"
        except re.error:
            pass  # invalid regex config — skip check silently

    return False, ""
