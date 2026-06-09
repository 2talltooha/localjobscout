from __future__ import annotations

import hashlib
import re

from localjobscout.db import Job


def _normalize(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def compute_job_hash(job: Job) -> str:
    """Deterministic hash of title + company + location (normalized)."""
    parts = "|".join([
        _normalize(job.title),
        _normalize(job.company),
        _normalize(job.location),
    ])
    return hashlib.sha256(parts.encode()).hexdigest()[:16]


def deduplicate(jobs: list[Job]) -> list[Job]:
    """Keep one job per (title, company, location) hash; prefer most recently posted."""
    seen: dict[str, Job] = {}
    for job in jobs:
        h = compute_job_hash(job)
        if h not in seen:
            seen[h] = job
        else:
            existing = seen[h]
            if job.posted_at and (
                not existing.posted_at or job.posted_at > existing.posted_at
            ):
                seen[h] = job
    return list(seen.values())
