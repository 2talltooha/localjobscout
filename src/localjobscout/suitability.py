"""LLM-based suitability scorer.

Assesses whether a job is an appropriate application target for this specific
applicant (first-year biology student, pre-medicine, Waterloo ON, no professional
lab experience, CPR/First Aid certified, trilingual EN/AR/FR).

Results are cached in the DB by job_hash — the API is never called twice for
the same job content. Silently skips when ANTHROPIC_API_KEY is not set or the
anthropic package is not installed.

When a ``ResumeProfile`` is provided, the static applicant context is placed in
a prompt-cached content block so the API only processes the job-specific part on
repeated calls — ~85% cost reduction when scoring many jobs in one session.

Public entry point: ``score_and_cache(job, resume_text, profile=None)`` — reads
from and writes to the DB (caller must have already called ``db.init_db()``).
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any

from localjobscout.db import Job

if TYPE_CHECKING:
    from localjobscout.profile import ResumeProfile

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You evaluate whether a job posting is a realistic application target for a "
    "specific job seeker. Be honest and realistic. Do not be encouraging if the "
    "role clearly requires credentials or experience the applicant does not have."
)

_JSON_INSTRUCTION = (
    'Respond with ONLY valid JSON on one line:\n'
    '{"suitability": <float 0.0-1.0>, "reason": "<one sentence, max 100 chars>", '
    '"qualified": "<yes|borderline|no>", "unmet": ["<requirement>", ...]}\n\n'
    "Scale:\n"
    "0.0 = completely unsuitable (wrong credentials, too senior)\n"
    "0.5 = stretch — possible but weak fit\n"
    "1.0 = excellent fit — entry-level, matches background directly\n\n"
    '"qualified" is a HARD eligibility check, separate from fit:\n'
    'no = posting explicitly requires a completed credential, professional\n'
    "  registration, program enrolment, or years of experience the applicant\n"
    "  does not have — an application would be rejected on screening\n"
    "borderline = a stated requirement is unmet but reads as preferred/assets,\n"
    "  or equivalent experience may be accepted\n"
    "yes = applicant meets every stated hard requirement\n"
    '"unmet" lists each unmet requirement in under 8 words (empty list if none).'
)

_USER_TEMPLATE = """\
APPLICANT RESUME (first 3000 chars):
{resume}

---
JOB TITLE: {title}
COMPANY: {company}
LOCATION: {location}
JOB DESCRIPTION (first 2000 chars):
{description}
---

Is this job posting a realistic and appropriate application target for THIS applicant?

Key applicant facts:
- First-year Biology (Honours) student, University of Guelph, pre-medicine
- No professional lab or clinical work experience
- Standard First Aid and CPR certified; Babysitting certification
- Trilingual: English, Arabic, French
- Camp counselor (2022-2023), peer tutor (2022-2025), MSA club leader
- Currently a student — available for part-time or summer positions only
- NOT enrolled in any professional program (pharmacy, nursing, paramedicine,
  lab technology) — cannot hold or obtain regulated-profession registration
  (e.g. Registered Pharmacy Technician, RN, MLT)

Consider: required credentials/licences, years of experience demanded,
seniority level, and whether the applicant's actual background is a plausible fit.
If the posting requires a completed diploma/degree, professional registration,
or enrolment in a specific professional program the applicant does not have,
score 0.2 or lower regardless of how well the subject matter matches.

"""
# NOTE: _JSON_INSTRUCTION is appended AFTER .format() in _call_api — it
# contains literal { } braces that would otherwise break str.format().


def _parse_response(
    raw: str, job_id: str
) -> tuple[float, str, str | None, list[str]] | None:
    """Parse the model's JSON → (score, reason, verdict, unmet) or None."""
    m = re.search(r"\{[^}]+\}", raw, re.DOTALL)
    if not m:
        logger.debug("suitability: no JSON in response for %s", job_id[:8])
        return None
    data = json.loads(m.group(0))
    score = float(data.get("suitability", 0.5))
    score = max(0.0, min(1.0, score))
    reason = str(data.get("reason", ""))[:200]
    verdict_raw = str(data.get("qualified", "")).strip().lower()
    verdict = verdict_raw if verdict_raw in ("yes", "borderline", "no") else None
    unmet_raw = data.get("unmet", [])
    unmet = [str(u)[:80] for u in unmet_raw if str(u).strip()] \
        if isinstance(unmet_raw, list) else []
    return score, reason, verdict, unmet


def _call_api(
    job: Job,
    resume_text: str,
    api_key: str,
) -> tuple[float, str, str | None, list[str]] | None:
    """Call the Anthropic API without prompt caching (plain text prompt)."""
    try:
        from localjobscout.llm_backend import make_client
        client: Any = make_client(api_key)
    except ImportError:
        return None

    try:
        prompt = _USER_TEMPLATE.format(
            resume=resume_text[:3000],
            title=job.title,
            company=job.company,
            location=job.location,
            description=(job.description or "")[:2000],
        ) + _JSON_INSTRUCTION
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(
            block.text for block in resp.content
            if getattr(block, "type", "") == "text"
        ).strip()
        return _parse_response(raw, job.id)

    except Exception as exc:  # noqa: BLE001
        logger.warning("suitability API call failed for %s: %s", job.id[:8], exc)
        return None


def _call_api_cached(
    job: Job,
    profile: ResumeProfile,
    api_key: str,
) -> tuple[float, str, str | None, list[str]] | None:
    """Call the API with the applicant profile in a prompt-cached content block.

    The first content block (profile + scoring instructions) is marked
    ``cache_control: ephemeral`` so the API caches it across calls for the same
    session. Only the per-job second block is re-processed each time.
    """
    try:
        from localjobscout.llm_backend import make_client
        client: Any = make_client(api_key)
    except ImportError:
        return None

    try:
        cached_block = (
            f"APPLICANT PROFILE:\n{profile.to_text()}\n\n"
            f"The applicant is NOT enrolled in any professional program "
            f"(pharmacy, nursing, paramedicine, lab technology) and cannot "
            f"hold regulated-profession registration (e.g. Registered "
            f"Pharmacy Technician, RN, MLT).\n\n"
            f"Consider: required credentials/licences, years of experience "
            f"demanded, seniority, and whether the applicant's background is "
            f"a plausible fit. If the posting requires a completed "
            f"diploma/degree, professional registration, or program enrolment "
            f"the applicant does not have, score 0.2 or lower regardless of "
            f"subject-matter match.\n\n"
            f"{_JSON_INSTRUCTION}"
        )
        job_block = (
            f"JOB TITLE: {job.title}\n"
            f"COMPANY: {job.company}\n"
            f"LOCATION: {job.location}\n"
            f"JOB DESCRIPTION (first 2000 chars):\n"
            f"{(job.description or '')[:2000]}\n\n"
            f"Is this job a realistic application target for this applicant?"
        )

        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": cached_block,
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        "type": "text",
                        "text": job_block,
                    },
                ],
            }],
        )
        raw = "".join(
            block.text for block in resp.content
            if getattr(block, "type", "") == "text"
        ).strip()
        return _parse_response(raw, job.id)

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "suitability cached API call failed for %s: %s — retrying without cache",
            job.id[:8], exc,
        )
        return _call_api(job, profile.to_text(), api_key)


def score_and_cache(
    job: Job,
    resume_text: str,
    profile: ResumeProfile | None = None,
) -> tuple[float, str] | None:
    """Score a job's suitability; cache result in DB. Returns (score, reason) or None.

    Skips the API call and returns None if:
    - ANTHROPIC_API_KEY is not set
    - anthropic package is not installed
    - the job already has a cached suitability_score in the DB

    When ``profile`` is provided, uses prompt caching for ~85% cost reduction
    across repeated calls in the same session.

    Rows scored before the qualification gate existed (suitability cached but
    no ``qualification_verdict``) are re-scored once to backfill the verdict.
    """
    from localjobscout import db as db_module

    cached = db_module.get_suitability(job.id)
    if cached is not None and db_module.get_qualification(job.id) is not None:
        return cached

    from localjobscout.llm_backend import use_cli

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key and not use_cli():
        return cached  # keep pre-gate cached score when API unavailable

    if profile is not None:
        result = _call_api_cached(job, profile, api_key)
    else:
        result = _call_api(job, resume_text, api_key)

    if result is None:
        return cached

    score, reason, verdict, unmet = result
    db_module.set_suitability(job.id, score, reason, verdict=verdict, unmet=unmet)
    return score, reason
