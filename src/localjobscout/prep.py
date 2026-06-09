"""Interview prep generator.

Uses ANTHROPIC_API_KEY to generate likely interview questions + suggested
answers tailored to the job description and resume. Falls back to a generic
template when the API key is absent.

`generate_and_save(job_id, settings)` is the public entry point. Saves a
Markdown file to data/applications/<source>-<short_id>-prep.md.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from localjobscout.config import Settings
from localjobscout.db import Job, find_job_by_short_id, get_job_by_id

logger = logging.getLogger(__name__)


class JobNotFoundError(Exception):
    pass


_GENERIC_QUESTIONS = [
    "Tell me about yourself and why you're interested in this role.",
    "What relevant experience do you have in a lab or clinical setting?",
    "Describe a time you had to follow a strict protocol or set of instructions.",
    "How do you handle mistakes or unexpected results in a scientific or work context?",
    "Why are you pursuing a career in medicine/healthcare?",
    "How do you prioritize tasks when you have multiple deadlines?",
    "Describe a situation where you worked as part of a team.",
    "What do you know about this organization?",
    "Where do you see yourself in 5 years?",
    "Do you have any questions for us?",
]


def _try_anthropic(job: Job, resume_text: str, api_key: str) -> str | None:
    try:
        from localjobscout.llm_backend import make_client
        client: Any = make_client(api_key)
    except ImportError:
        return None
    try:
        prompt = (
            "You are helping a first-year Biological Science (Honours) student "
            "at the University of Guelph prepare for a job interview. "
            "Generate exactly 10 likely interview questions for the job below, "
            "then for each question write a 2-3 sentence suggested answer "
            "grounded in the resume provided. Do NOT invent specific "
            "achievements that are not in the resume. Format output as Markdown "
            "with ## Q1, ## Q2, ... headings followed by **Suggested answer:** "
            "paragraphs. Output nothing else.\n\n"
            f"---\nRESUME:\n{resume_text[:3000]}\n"
            f"---\nJOB TITLE: {job.title}\n"
            f"COMPANY: {job.company}\n"
            f"LOCATION: {job.location}\n"
            f"JOB DESCRIPTION:\n{job.description[:4000]}"
        )
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            block.text
            for block in response.content
            if getattr(block, "type", "") == "text"
        )
        return text.strip() if text else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("anthropic prep API failed: %s", exc)
        return None


def _generic_prep(job: Job) -> str:
    lines = [f"## Interview Prep — {job.title} @ {job.company}\n"]
    for i, q in enumerate(_GENERIC_QUESTIONS, 1):
        lines.append(
            f"## Q{i}: {q}\n\n"
            "**Suggested answer:** *(write your own based on your experience)*\n"
        )
    return "\n".join(lines)


def _resolve_job(job_id: str) -> Job:
    job = get_job_by_id(job_id) or find_job_by_short_id(job_id)
    if job is None:
        raise JobNotFoundError(f"No job found matching id prefix {job_id!r}")
    return job


def _load_resume(settings: Settings) -> str:
    path = settings.resume_path
    if not path.exists():
        raise FileNotFoundError(f"Resume not found at {path}")
    return path.read_text(encoding="utf-8")


def generate_and_save(job_id: str, *, settings: Settings) -> Path:
    job = _resolve_job(job_id)
    resume_text = _load_resume(settings)

    from localjobscout.llm_backend import use_cli

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    body: str
    if api_key or use_cli():
        result = _try_anthropic(job, resume_text, api_key)
        body = result if result else _generic_prep(job)
    else:
        body = _generic_prep(job)

    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    content = (
        f"# Interview Prep — {job.title}\n\n"
        f"- **Company:** {job.company}\n"
        f"- **Location:** {job.location}\n"
        f"- **Source:** {job.source}\n"
        f"- **URL:** {job.url}\n"
        f"- **Generated:** {now}\n\n"
        f"---\n\n"
        f"{body}\n"
    )

    out_dir = settings.db_path.parent / "applications"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = f"{job.source}-{job.id[:8]}-prep.md"
    out_path = out_dir / slug
    out_path.write_text(content, encoding="utf-8")
    return out_path
