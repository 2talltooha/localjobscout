"""Resume tailoring suggestions.

Analyzes a job description against the applicant's profile and generates
honest, actionable suggestions to tailor the resume for that specific posting
— without fabricating experience.

Public entry point: ``generate_tailoring(job, resume_text, profile)``
Save output with ``save_tailoring(job, suggestions, output_dir)``.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from localjobscout.db import Job
from localjobscout.profile import ResumeProfile

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a career coach helping a student tailor their resume for a specific "
    "job posting. You MUST NOT suggest claiming experience, certifications, or "
    "skills that are not present in the provided profile. Every suggestion must "
    "be an honest reframing of real, existing experience."
)

_PROMPT_TEMPLATE = """\
Analyze this job posting and suggest how the applicant can honestly tailor
their resume to be more competitive — without fabricating anything.

APPLICANT PROFILE:
{profile}

JOB TITLE: {title}
COMPANY: {company}
JOB DESCRIPTION:
{description}

Write a concise resume tailoring report in Markdown with exactly these sections:

## Keywords to Add
Keywords from the job description that the applicant can honestly incorporate
because they have the underlying experience — they just haven't used that phrasing.
List each keyword and the resume section where it fits.

## Sections to Emphasize
Which parts of the applicant's existing experience are most relevant to this role?
Suggest how to reorder, rename, or expand existing bullet points.

## Honest Reframings
Specific before → after examples showing how to describe existing experience
(tutoring, camp counselor, CPR cert, coursework, languages) in language that
resonates with this job's requirements.

## Gaps (Do Not Fabricate)
What this role wants that the applicant genuinely does not have.
List clearly so they know what NOT to claim.

## Verdict
2 sentences: how strong is this application, and the single highest-impact change.
"""


class TailoringError(Exception):
    pass


def generate_tailoring(
    job: Job,
    resume_text: str,
    profile: ResumeProfile | None,
) -> str:
    """Generate resume tailoring suggestions for a specific job.

    Returns Markdown string. Raises TailoringError on missing key or API failure.
    Falls back to raw resume text when profile is None.
    """
    from localjobscout.llm_backend import make_client, use_cli

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key and not use_cli():
        raise TailoringError(
            "ANTHROPIC_API_KEY not set — tailoring requires the Anthropic API "
            "(or set LOCALJOBSCOUT_USE_CLI=1 to use the claude CLI subscription)."
        )

    try:
        client: Any = make_client(api_key)
    except ImportError:
        raise TailoringError("anthropic package not installed.")

    profile_text = (
        profile.to_text() if profile is not None
        else f"RESUME (raw):\n{resume_text[:2000]}"
    )

    prompt = _PROMPT_TEMPLATE.format(
        profile=profile_text,
        title=job.title,
        company=job.company,
        description=(job.description or "")[:3000],
    )

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1400,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            block.text for block in resp.content
            if getattr(block, "type", "") == "text"
        ).strip()
        if not text:
            raise TailoringError("API returned empty response.")
        return text
    except TailoringError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise TailoringError(f"API call failed: {exc}") from exc


def render_markdown(job: Job, suggestions: str) -> str:
    """Wrap suggestions with a metadata header."""
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"# Resume Tailoring — {job.title}\n\n"
        f"- **Company:** {job.company}\n"
        f"- **Location:** {job.location}\n"
        f"- **Source:** {job.source}\n"
        f"- **URL:** {job.url}\n"
        f"- **Generated:** {now}\n\n"
        f"---\n\n"
        f"{suggestions}\n"
    )


def save_tailoring(job: Job, suggestions: str, output_dir: Path) -> Path:
    """Save tailoring report to <output_dir>/<source>-<id8>-resume-tips.md."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{job.source}-{job.id[:8]}-resume-tips.md"
    path.write_text(render_markdown(job, suggestions), encoding="utf-8")
    return path
