"""Application-tracking glue.

Thin orchestration layer that:
- Resolves a short id prefix to a Job (so the user can type the first 8
  chars rather than a 64-char hex hash).
- Validates application_status against the allowed set.
- Writes generated cover-letter files to `data/applications/` and
  stores the path on the row.

The DB primitives live in `db.py`; the cover-letter rendering lives in
`cover_letter.py`. This module ties them together for the CLI.
"""
from __future__ import annotations

import logging
from pathlib import Path

from localjobscout import cover_letter as cover_letter_module
from localjobscout import db as db_module
from localjobscout import resume as resume_module
from localjobscout.config import Settings
from localjobscout.db import APPLICATION_STATUSES, Job

logger = logging.getLogger(__name__)

_APPLICATIONS_DIR = Path("data/applications")


class JobNotFoundError(LookupError):
    """No job matches the given id / prefix."""


class AmbiguousJobError(LookupError):
    """More than one job matches the given id prefix."""


class InvalidStatusError(ValueError):
    """The given status is not one of APPLICATION_STATUSES."""


def resolve_job(id_or_prefix: str) -> Job:
    """Resolve a full id OR a prefix (>=4 chars) to a single Job."""
    job = db_module.get_job_by_id(id_or_prefix)
    if job is not None:
        return job
    if len(id_or_prefix) < 4:
        raise JobNotFoundError(
            f"id prefix too short to disambiguate: {id_or_prefix!r} "
            f"(need at least 4 hex chars)"
        )
    job = db_module.find_job_by_short_id(id_or_prefix)
    if job is None:
        raise JobNotFoundError(
            f"no job (or more than one) matches {id_or_prefix!r}"
        )
    return job


def mark_status(
    id_or_prefix: str,
    status: str | None,
    *,
    notes: str | None = None,
) -> Job:
    if status is not None and status not in APPLICATION_STATUSES:
        raise InvalidStatusError(
            f"status must be one of {APPLICATION_STATUSES} or None "
            f"(got {status!r})"
        )
    job = resolve_job(id_or_prefix)
    db_module.update_application_status(job.id, status, notes=notes)
    return resolve_job(job.id)


def generate_cover_letter(
    id_or_prefix: str,
    *,
    settings: Settings,
    output_dir: Path = _APPLICATIONS_DIR,
) -> tuple[Path, str]:
    """Generate + write a cover-letter file. Returns (path, backend)."""
    job = resolve_job(id_or_prefix)
    resume_text = resume_module.load_resume(settings.resume_path)
    body, backend = cover_letter_module.generate(
        job, settings=settings, resume_text=resume_text
    )
    markdown = cover_letter_module.render_markdown(job, body, backend)

    output_dir.mkdir(parents=True, exist_ok=True)
    safe_source = job.source.replace("/", "_")
    short = job.id[:8]
    path = output_dir / f"{safe_source}-{short}.md"
    path.write_text(markdown, encoding="utf-8")
    db_module.set_cover_letter_path(job.id, str(path))
    return path, backend


def list_applications(status: str | None = None) -> list[Job]:
    return db_module.get_applied_jobs(status=status)
