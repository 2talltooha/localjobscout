"""Cover-letter generator with two backends.

1. Anthropic API (``ANTHROPIC_API_KEY`` env var) — best quality. Sends the
   resume + job description + a system prompt asking for a tailored
   one-page cover letter. Costs roughly $0.01–0.02 per call.

2. Template fallback (default) — fully offline. Picks one of a handful
   of premed-targeted body templates by matching keywords against the
   job title, then string-substitutes name / company / title / focus
   keywords into the chosen template.

The output is plain Markdown so the user can paste into Word / Google
Docs / an ATS textarea. The file is written to
``data/applications/<source>-<short_id>.md`` by the caller.

``generate(...)`` is the single public entry point. It picks the backend
automatically:

- If ``ANTHROPIC_API_KEY`` is set AND ``anthropic`` is importable, use the
  API. Any error (rate-limit, network, missing model) falls back to the
  template.
- Otherwise, template path is used unconditionally.

``validate(letter, resume_text)`` checks for claims that are not supported
by the resume text — e.g. WHMIS certification, retail experience, years of
specific experience. Returns a list of warning strings (empty = clean).
"""
from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

from localjobscout.config import Settings
from localjobscout.db import Job

logger = logging.getLogger(__name__)


_DEFAULT_USER_NAME = "Taha El Ghadi"
_DEFAULT_USER_PROFILE = (
    "first-year Biological Science (Honours) student at the University of "
    "Guelph, pursuing pre-medicine"
)

# ─── Templates ────────────────────────────────────────────────────────────────
# All templates are grounded in the actual resume:
#   - Certified in Standard First Aid and CPR (NOT WHMIS)
#   - Camp counselor, peer tutor, MSA club leader
#   - NO retail/customer service job
#   - NO lab experience

_TEMPLATES: list[tuple[tuple[str, ...], str]] = [
    (
        (
            "lab", "laboratory", "technician", "research assistant",
            "scientist", "wet lab", "biotech", "pcr", "cell culture",
            "microbiology", "biochemistry", "chemistry",
        ),
        (
            "Dear Hiring Team,\n\n"
            "I am writing to express my interest in the **{title}** "
            "position at **{company}**. As a {profile}, I am drawn to "
            "this role because it offers the kind of hands-on scientific "
            "exposure I am actively building toward in my degree.\n\n"
            "My coursework in cell biology, biochemistry, and general "
            "chemistry has given me a working foundation in {keywords}. "
            "I am comfortable following written protocols closely, "
            "maintaining careful records, and asking clarifying questions "
            "when unsure — habits I have also applied through my Standard "
            "First Aid and CPR certification. Outside of class I tutor "
            "peers and lead student-society initiatives, which has "
            "sharpened my ability to communicate clearly and work reliably "
            "in a team.\n\n"
            "Thank you for considering my application. I would be "
            "happy to provide references, transcripts, or any additional "
            "information at your convenience.\n\n"
            "Sincerely,\n{user_name}"
        ),
    ),
    (
        (
            "healthcare", "clinical", "hospital", "patient", "nurse",
            "medical", "phlebotomy", "scribe", "orderly", "care",
            "specimen", "pharmacy", "respiratory", "rehabilitation",
        ),
        (
            "Dear Hiring Team,\n\n"
            "I am writing to apply for the **{title}** position at "
            "**{company}**. As a {profile}, gaining direct patient "
            "contact and clinical exposure is central to the path I am "
            "preparing for, and the chance to contribute at {company} "
            "is exactly what I am looking for.\n\n"
            "I bring strong interpersonal awareness developed through "
            "leading the MSA student organization and tutoring peers "
            "across multiple subject areas. I hold up-to-date Standard "
            "First Aid and CPR certifications and have built familiarity "
            "with {keywords} through my coursework. I understand that "
            "healthcare environments demand discretion, attention to "
            "detail, and the ability to stay calm under pressure — and I "
            "am committed to meeting that standard from day one.\n\n"
            "Thank you for your time and consideration. I would be "
            "glad to discuss how my background lines up with your "
            "team's needs.\n\n"
            "Sincerely,\n{user_name}"
        ),
    ),
    (
        (
            "tutor", "teaching", "instructor", "education", "academic",
            "training", "outreach", "summer", "intern",
        ),
        (
            "Dear Hiring Team,\n\n"
            "I am applying for the **{title}** position at **{company}**. "
            "As a {profile}, I have spent the past few years tutoring "
            "peers and supporting learners one-on-one, and I would welcome "
            "the chance to do that work more formally with {company}.\n\n"
            "I am comfortable explaining concepts in plain language, "
            "adapting my pace to the learner, and preparing materials in "
            "advance. My background in biology and chemistry directly "
            "supports topics like {keywords}, and my Standard First Aid "
            "and CPR training adds an extra safety layer when working with "
            "participants of any age. I show up on time, follow program "
            "guidelines, and treat everyone I work with respectfully.\n\n"
            "Thank you for considering me. I would be happy to share "
            "references or further detail at your convenience.\n\n"
            "Sincerely,\n{user_name}"
        ),
    ),
]

_FALLBACK_TEMPLATE = (
    "Dear Hiring Team,\n\n"
    "I am writing to express my interest in the **{title}** position at "
    "**{company}**. As a {profile}, I am looking for opportunities to "
    "contribute meaningfully outside of class while building skills that "
    "translate directly into my long-term goals.\n\n"
    "I take pride in showing up reliably, learning quickly from written "
    "or verbal guidance, and asking clarifying questions when I am "
    "unsure. My academic background touches on {keywords}, and I hold "
    "Standard First Aid and CPR certifications. I would be glad to bring "
    "that combination of work ethic and trainable skills to **{company}**.\n\n"
    "Thank you for your time and consideration.\n\n"
    "Sincerely,\n{user_name}"
)

# ─── Validation ───────────────────────────────────────────────────────────────

# (pattern, description of false claim, skip_keyword)
#
# skip_keyword: a substring whose presence in the reference text (resume or
# master resume) means the claim is legitimate and should NOT be flagged. This
# is what makes the validator reusable against the structured master resume —
# a fact that genuinely exists in the master (e.g. "customer service", "lab")
# passes, while the same claim against a resume that lacks it is still caught.
# ``None`` = always flag (claiming specific years of experience is never valid
# for this applicant).
_FALSE_CLAIM_PATTERNS: list[tuple[re.Pattern[str], str, str | None]] = [
    (re.compile(r"\bwhmis\b", re.I),
     "claims WHMIS certification (not in resume)", "whmis"),
    (re.compile(r"\bretail\b", re.I),
     "mentions retail experience (not in resume)", "retail"),
    (re.compile(r"\bcustomer.{0,20}service\b", re.I),
     "claims customer service role (not in resume)", "customer service"),
    (
        re.compile(
            r"\b\d+\s*(?:years?|yrs?)\s+(?:of\s+)?"
            r"(?:lab|clinical|research|professional|relevant)\b",
            re.I,
        ),
        "claims specific years of professional/lab experience",
        None,
    ),
    (re.compile(r"\bhands.on\s+(?:lab|bench|research|clinical)\s+experience\b", re.I),
     "claims hands-on lab/clinical experience (not in resume)", None),
    # Bare claims without a number — e.g. "strong laboratory background"
    (re.compile(r"\blab\s+(?:experience|background|work|skills)\b", re.I),
     "claims lab experience/background (not in resume)", "lab"),
    (re.compile(r"\blaboratory\s+(?:experience|background|work|skills)\b", re.I),
     "claims laboratory experience/background (not in resume)", "laboratory"),
    (re.compile(r"\bbench\s+(?:experience|work|skills)\b", re.I),
     "claims bench experience (not in resume)", "bench"),
]


def validate(
    letter: str,
    resume_text: str,
    extra_forbidden: list[str] | None = None,
) -> list[str]:
    """Check cover letter for claims not supported by the resume.

    Returns a list of warning strings. Empty list = clean.

    Hardcoded patterns (``_FALSE_CLAIM_PATTERNS``) flag things that are
    definitively absent from any first-year student's resume (WHMIS, retail,
    specific years of lab/bench experience).

    ``extra_forbidden`` (from ``cover_letter.forbidden_claims`` in config.yaml)
    lets users add/remove phrases without touching code.
    """
    warnings: list[str] = []
    resume_lower = resume_text.lower()

    for pattern, description, skip_keyword in _FALSE_CLAIM_PATTERNS:
        if not pattern.search(letter):
            continue
        # Skip when the claim is backed by the reference text (resume/master).
        if skip_keyword is not None and skip_keyword in resume_lower:
            continue
        warnings.append(description)

    # User-configured forbidden phrases (config.yaml cover_letter.forbidden_claims)
    if extra_forbidden:
        letter_lower = letter.lower()
        for phrase in extra_forbidden:
            if phrase.strip() and phrase.lower() in letter_lower:
                warnings.append(f"forbidden phrase in config: '{phrase}'")

    return warnings


# ─── Helpers ──────────────────────────────────────────────────────────────────

# Maps focus keywords → natural noun phrases suitable for sentence insertion.
# Empty string = skip (keyword doesn't read as a noun phrase).
_KEYWORD_PHRASES: dict[str, str] = {
    "research": "research methodology",
    "lab": "laboratory techniques",
    "laboratory": "laboratory techniques",
    "pharmacy": "pharmacy operations",
    "clinical": "clinical terminology",
    "biology": "biological sciences",
    "health": "health sciences",
    "patient": "patient care principles",
    "tutor": "peer tutoring",
    "premed": "pre-medical studies",
    "science": "scientific methods",
    "assistant": "",
    "entry level": "",
}


def _pick_template(title: str) -> str:
    lower = title.lower()
    for keywords, template in _TEMPLATES:
        if any(kw in lower for kw in keywords):
            return template
    return _FALLBACK_TEMPLATE


def _relevant_keywords(job: Job, focus_keywords: list[str]) -> str:
    """Pick 3–4 natural phrases from focus keywords present in the job text."""
    blob = (job.title + " " + job.description).lower()
    phrases: list[str] = []
    seen: set[str] = set()
    for kw in focus_keywords:
        if kw not in blob:
            continue
        phrase = _KEYWORD_PHRASES.get(kw, kw)
        if not phrase or phrase in seen:
            continue
        phrases.append(phrase)
        seen.add(phrase)
        if len(phrases) >= 4:
            break
    if not phrases:
        phrases = ["scientific methods", "data collection", "patient care"]
    return ", ".join(phrases)


def _resolve_user_name(resume_text: str) -> str:
    """Extract applicant name from first non-empty line of resume."""
    for line in resume_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if 2 <= len(stripped.split()) <= 5 and not re.search(
            r"[@:|]|\d{4}", stripped
        ):
            return stripped
        break
    return _DEFAULT_USER_NAME


def _render_template(
    job: Job,
    *,
    resume_text: str,
    focus_keywords: list[str],
    user_profile: str = _DEFAULT_USER_PROFILE,
) -> str:
    body = _pick_template(job.title)
    return body.format(
        title=job.title.strip() or "this role",
        company=job.company.strip() or "your organization",
        profile=user_profile,
        keywords=_relevant_keywords(job, focus_keywords),
        user_name=_resolve_user_name(resume_text),
    )


# ─── Anthropic backend ────────────────────────────────────────────────────────

_API_SYSTEM = (
    "You write honest, grounded cover letters. You MUST NOT invent experience, "
    "certifications, or qualifications that are not explicitly stated in the "
    "provided resume. If you are tempted to claim something not in the resume, "
    "omit it entirely."
)


def _try_anthropic(
    job: Job,
    *,
    resume_text: str,
    api_key: str,
) -> str | None:
    """Try the Anthropic API; return None on any failure."""
    try:
        from localjobscout.llm_backend import make_client
        client: Any = make_client(api_key)
    except ImportError:
        return None

    try:
        prompt = (
            "Write a one-page cover letter (max 3 short paragraphs) tailored to "
            "the job description below. The applicant is a first-year Biological "
            "Science (Honours) student at the University of Guelph pursuing "
            "pre-medicine. Use a professional but warm tone. Output Markdown only "
            "— no preamble, no closing commentary.\n\n"
            "CRITICAL: Do NOT claim WHMIS, retail experience, or any lab/clinical "
            "experience that is not in the resume. Only reference what is "
            "explicitly in the resume.\n\n"
            f"---\nRESUME:\n{resume_text}\n"
            f"---\nJOB TITLE: {job.title}\n"
            f"COMPANY: {job.company}\n"
            f"LOCATION: {job.location}\n"
            f"JOB DESCRIPTION:\n{job.description[:4000]}"
        )
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=900,
            system=_API_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            block.text for block in response.content
            if getattr(block, "type", "") == "text"
        )
        return text.strip() if text else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("anthropic cover-letter API failed: %s", exc)
        return None


# ─── Public API ───────────────────────────────────────────────────────────────


def generate(
    job: Job,
    *,
    settings: Settings,
    resume_text: str,
) -> tuple[str, str]:
    """Generate a cover letter. Returns (body, backend) where backend is
    'anthropic' or 'template'."""
    from localjobscout.llm_backend import use_cli

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if api_key or use_cli():
        api_text = _try_anthropic(job, resume_text=resume_text, api_key=api_key)
        if api_text:
            return api_text, "anthropic"

    text = _render_template(
        job,
        resume_text=resume_text,
        focus_keywords=settings.focus.keywords,
    )
    return text, "template"


def render_markdown(job: Job, body: str, backend: str) -> str:
    """Wrap the generated body with a metadata header."""
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"# Cover Letter — {job.title}\n\n"
        f"- **Company:** {job.company}\n"
        f"- **Location:** {job.location}\n"
        f"- **Source:** {job.source}\n"
        f"- **URL:** {job.url}\n"
        f"- **Generated:** {now} (backend: {backend})\n\n"
        f"---\n\n"
        f"{body}\n"
    )
