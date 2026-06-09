from __future__ import annotations

import re
from datetime import date
from typing import TypedDict

from pydantic import BaseModel

from localjobscout.db import Job

SKILLS_LIST = [
    "Python", "JavaScript", "TypeScript", "Java", "Go", "Rust", "C++", "C#",
    "Ruby", "PHP", "Swift", "Kotlin",
    "React", "Vue.js", "Angular", "Node.js", "Django", "Flask", "FastAPI",
    "Spring", "Rails",
    "AWS", "Azure", "GCP", "Docker", "Kubernetes", "Terraform", "Linux",
    "PostgreSQL", "MySQL", "SQLite", "MongoDB", "Redis", "Elasticsearch",
    "Kafka", "RabbitMQ",
    "Machine Learning", "TensorFlow", "PyTorch", "scikit-learn",
    "SQL", "NoSQL", "Git", "CI/CD", "REST", "GraphQL",
]

_SKILL_ALIASES: dict[str, str] = {
    "js": "JavaScript",
    "ts": "TypeScript",
    "ml": "Machine Learning",
    "k8s": "Kubernetes",
    "vue": "Vue.js",
    "node": "Node.js",
    "nodejs": "Node.js",
    "postgres": "PostgreSQL",
    "pg": "PostgreSQL",
    "mongo": "MongoDB",
    "sklearn": "scikit-learn",
}

_SKILLS_LOWER: dict[str, str] = {s.lower(): s for s in SKILLS_LIST}
_ALIASES_LOWER: dict[str, str] = {k.lower(): v for k, v in _SKILL_ALIASES.items()}

_CURRENCY_SYMBOLS: dict[str, str] = {
    "CA$": "CAD",
    "C$": "CAD",
    "£": "GBP",
    "€": "EUR",
    "$": "USD",
}
_SYMBOL_RE = re.compile(r"CA\$|C\$|[£€$]")
_NUMBER_RE = re.compile(r"[\d,]+(?:\.\d+)?k?", re.IGNORECASE)
_HOURLY_RE = re.compile(r"\bhour\b|\bhr\b", re.IGNORECASE)


class SalaryInfo(TypedDict, total=False):
    min: int | None
    max: int | None
    currency: str
    hourly: bool


def _parse_number(s: str) -> int | None:
    """Parse a numeric token to int, or None if it isn't a real number.

    The number regex can match comma- or dot-only tokens (e.g. ``","``) which
    become empty after stripping separators; those return None rather than
    crashing the whole scan.
    """
    s = s.replace(",", "").strip()
    if not s:
        return None
    try:
        if s.lower().endswith("k"):
            return int(float(s[:-1]) * 1000)
        return int(float(s))
    except ValueError:
        return None


def parse_salary(salary_str: str) -> SalaryInfo:
    if not salary_str or not salary_str.strip():
        return {"min": None, "max": None, "currency": "USD", "hourly": False}

    text = salary_str.strip()

    currency = "USD"
    sym_match = _SYMBOL_RE.search(text)
    if sym_match:
        sym = sym_match.group()
        currency = _CURRENCY_SYMBOLS.get(sym, "USD")

    hourly = bool(_HOURLY_RE.search(text))

    raw_nums = _NUMBER_RE.findall(text)
    numbers = [v for n in raw_nums if (v := _parse_number(n)) is not None]
    if not numbers:
        return {"min": None, "max": None, "currency": currency, "hourly": hourly}

    min_val = numbers[0]
    max_val = numbers[-1] if len(numbers) > 1 else numbers[0]
    if max_val < min_val:
        min_val, max_val = max_val, min_val

    return {"min": min_val, "max": max_val, "currency": currency, "hourly": hourly}


def annualize_salary(salary: SalaryInfo) -> tuple[int | None, int | None]:
    """Convert hourly range to annual (40h/week × 52 weeks = 2080h/year)."""
    factor = 40 * 52
    if salary.get("hourly"):
        mn = salary.get("min")
        mx = salary.get("max")
        return (mn * factor if mn is not None else None,
                mx * factor if mx is not None else None)
    return salary.get("min"), salary.get("max")


# Captures a money-bearing snippet: $25, $25-$30, CA$50,000, $50k to $60k,
# optionally followed by a period word (per hour / /yr / annually).
_SALARY_SNIPPET_RE = re.compile(
    r"(?:CA\$|C\$|\$)\s?\d[\d,.]*k?"
    r"(?:\s?(?:-|–|—|to)\s?(?:CA\$|C\$|\$)?\s?\d[\d,.]*k?)?"
    r"(?:\s?(?:per\s+|/\s?)?(?:hour|hr|year|yr|annum|annually|hourly))?",
    re.IGNORECASE,
)
# A snippet is only treated as hourly when an hourly word is actually near it.
_HOURLY_CONTEXT_RE = re.compile(r"\b(?:hour|hr|hourly)\b", re.IGNORECASE)


def extract_salary_from_text(text: str) -> tuple[int | None, int | None]:
    """Find the first salary figure in free text and return (min, max) annualized.

    Hourly rates are converted to annual (2080h/year). Returns (None, None)
    when no salary is found. Bare ``$`` figures with no hourly/annual context
    are assumed annual only when ≥ 1000 (to avoid treating "$25" as $25/yr);
    small bare figures are treated as hourly.
    """
    if not text:
        return None, None

    m = _SALARY_SNIPPET_RE.search(text)
    if not m:
        return None, None

    snippet = m.group(0)
    info = parse_salary(snippet)

    # parse_salary only marks hourly when the snippet itself contains hour/hr.
    # If not, sniff the surrounding window for hourly context.
    if not info.get("hourly"):
        window = text[max(0, m.start() - 20): m.end() + 20]
        mn = info.get("min")
        is_small = mn is not None and mn < 1000
        if _HOURLY_CONTEXT_RE.search(window) or is_small:
            info["hourly"] = True

    return annualize_salary(info)


_MONTHS: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Trigger phrases that introduce a deadline, e.g. "apply by", "closing date".
_DEADLINE_TRIGGER = (
    r"(?:deadline|apply\s+by|applications?\s+close(?:s|d)?(?:\s+on)?|"
    r"closing\s+date|close[sd]?\s+on|must\s+apply\s+by|due\s+(?:by|date))"
)
# Date forms: 2026-01-15 | 15/01/2026 | January 15, 2026 | 15 January 2026
_DATE_ISO = r"(\d{4})-(\d{1,2})-(\d{1,2})"
_DATE_SLASH = r"(\d{1,2})/(\d{1,2})/(\d{4})"
_MONTH_NAMES = (
    "january|february|march|april|may|june|july|august|september|"
    "october|november|december|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec"
)
_DATE_MONTH_FIRST = rf"({_MONTH_NAMES})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})"
_DATE_DAY_FIRST = rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_NAMES})\.?,?\s+(\d{{4}})"

_DEADLINE_RES: list[re.Pattern[str]] = [
    re.compile(rf"{_DEADLINE_TRIGGER}[:\s]*{_DATE_ISO}", re.IGNORECASE),
    re.compile(rf"{_DEADLINE_TRIGGER}[:\s]*{_DATE_MONTH_FIRST}", re.IGNORECASE),
    re.compile(rf"{_DEADLINE_TRIGGER}[:\s]*{_DATE_DAY_FIRST}", re.IGNORECASE),
    re.compile(rf"{_DEADLINE_TRIGGER}[:\s]*{_DATE_SLASH}", re.IGNORECASE),
]


def _safe_date(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def extract_deadline(text: str) -> str | None:
    """Find an application deadline in free text. Returns ISO 'YYYY-MM-DD' or None.

    Recognises a trigger phrase ("apply by", "deadline", "closing date", …)
    followed by a date in ISO, slash, "Month DD, YYYY", or "DD Month YYYY" form.
    """
    if not text:
        return None

    for idx, pat in enumerate(_DEADLINE_RES):
        m = pat.search(text)
        if not m:
            continue
        g = m.groups()
        if idx == 0:  # ISO: year, month, day
            return _safe_date(int(g[0]), int(g[1]), int(g[2]))
        if idx == 1:  # month-name first: month, day, year
            month = _MONTHS.get(g[0][:4].lower()) or _MONTHS.get(g[0][:3].lower())
            if month:
                return _safe_date(int(g[2]), month, int(g[1]))
        elif idx == 2:  # day first: day, month-name, year
            month = _MONTHS.get(g[1][:4].lower()) or _MONTHS.get(g[1][:3].lower())
            if month:
                return _safe_date(int(g[2]), month, int(g[0]))
        elif idx == 3:  # slash DD/MM/YYYY (Canadian convention)
            return _safe_date(int(g[2]), int(g[1]), int(g[0]))
    return None


def extract_skills(title: str, description: str, company: str = "") -> list[str]:
    text = f"{title} {description} {company}".lower()
    found: dict[str, str] = {}

    for alias, canonical in _ALIASES_LOWER.items():
        if re.search(r"\b" + re.escape(alias) + r"\b", text):
            found[canonical.lower()] = canonical

    for skill_lower, skill in _SKILLS_LOWER.items():
        if skill_lower not in found:
            if re.search(r"\b" + re.escape(skill_lower) + r"\b", text):
                found[skill_lower] = skill

    return list(found.values())[:10]


class JobFilter(BaseModel):
    min_salary: int | None = None
    max_salary: int | None = None
    required_skills: list[str] = []
    excluded_skills: list[str] = []
    allowed_job_types: list[str] = []
    excluded_keywords: list[str] = []

    def matches(self, job: Job) -> bool:
        if self.min_salary is not None and job.salary_max is not None:
            if job.salary_max < self.min_salary:
                return False

        if self.max_salary is not None and job.salary_min is not None:
            if job.salary_min > self.max_salary:
                return False

        job_skills_lower = {s.lower() for s in job.skills}
        for skill in self.required_skills:
            if skill.lower() not in job_skills_lower:
                return False
        for skill in self.excluded_skills:
            if skill.lower() in job_skills_lower:
                return False

        if self.allowed_job_types and job.job_type:
            if job.job_type not in self.allowed_job_types:
                return False

        if self.excluded_keywords:
            text = f"{job.title} {job.description}".lower()
            for kw in self.excluded_keywords:
                if kw.lower() in text:
                    return False

        return True
