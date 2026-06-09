"""AI resume profile parser with file-based cache.

Parses the resume once via Anthropic API into a structured profile and caches
it to data/resume_profile.json. Cache invalidates when the resume file is newer.

The profile feeds into suitability scoring (prompt caching) and tailoring.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_CACHE = Path("data/resume_profile.json")

_SYSTEM = (
    "You extract structured information from resumes. "
    "Output only valid JSON. No commentary, no markdown, no backticks."
)

_PROMPT_TEMPLATE = """\
Extract a structured applicant profile from the resume below for use in
job-suitability scoring and resume tailoring.

Output ONLY this JSON object (exact keys, no extras):
{{
  "name": "<full name from resume>",
  "education": "<current degree, year, school>",
  "skills": ["<every concrete skill, tool, or technique mentioned>"],
  "certifications": ["<e.g. Standard First Aid, CPR, Babysitting>"],
  "languages": ["<spoken/written languages>"],
  "experience_level": "entry-level",
  "experience_summary": "<2 sentences: what paid/volunteer work they have done>",
  "target_roles": ["<job types this person is realistically suited for>"],
  "avoid_roles": ["<job types requiring credentials/experience they do not have>"]
}}

RESUME:
{resume}
"""

_PROFILE_FIELDS = {
    "name", "education", "skills", "certifications", "languages",
    "experience_level", "experience_summary", "target_roles", "avoid_roles",
}


@dataclass
class ResumeProfile:
    name: str = ""
    education: str = ""
    skills: list[str] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    experience_level: str = "entry-level"
    experience_summary: str = ""
    target_roles: list[str] = field(default_factory=list)
    avoid_roles: list[str] = field(default_factory=list)

    def to_text(self) -> str:
        """Render profile as concise text for LLM prompts."""
        lines = [
            f"Name: {self.name}",
            f"Education: {self.education}",
            f"Experience level: {self.experience_level}",
            f"Experience: {self.experience_summary}",
            f"Skills: {', '.join(self.skills)}",
            f"Certifications: {', '.join(self.certifications)}",
            f"Languages: {', '.join(self.languages)}",
            f"Suited for: {', '.join(self.target_roles)}",
            f"Not suited for: {', '.join(self.avoid_roles)}",
        ]
        return "\n".join(lines)


def _call_api(resume_text: str, api_key: str) -> ResumeProfile | None:
    """Call Anthropic API to parse resume into structured profile."""
    try:
        from localjobscout.llm_backend import make_client
        client: Any = make_client(api_key)
    except ImportError:
        return None

    try:
        prompt = _PROMPT_TEMPLATE.format(resume=resume_text[:4000])
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(
            block.text for block in resp.content
            if getattr(block, "type", "") == "text"
        ).strip()

        # Strip markdown code fences if model wraps output anyway
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()

        data = json.loads(raw)
        return ResumeProfile(
            name=str(data.get("name", "")),
            education=str(data.get("education", "")),
            skills=[str(s) for s in data.get("skills", [])],
            certifications=[str(s) for s in data.get("certifications", [])],
            languages=[str(s) for s in data.get("languages", [])],
            experience_level=str(data.get("experience_level", "entry-level")),
            experience_summary=str(data.get("experience_summary", "")),
            target_roles=[str(s) for s in data.get("target_roles", [])],
            avoid_roles=[str(s) for s in data.get("avoid_roles", [])],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("profile: API call failed: %s", exc)
        return None


def _is_cache_valid(resume_path: Path, cache_path: Path) -> bool:
    """Return True if cache exists and is at least as new as the resume file."""
    if not cache_path.exists():
        return False
    if not resume_path.exists():
        return False
    return cache_path.stat().st_mtime >= resume_path.stat().st_mtime


def load_or_parse(
    resume_path: Path,
    resume_text: str,
    cache_path: Path = _DEFAULT_CACHE,
) -> ResumeProfile | None:
    """Load cached profile or re-parse via API when stale/missing.

    Returns None when ANTHROPIC_API_KEY is absent or the API call fails.
    Callers should handle None gracefully (fall back to raw resume text).
    """
    if _is_cache_valid(resume_path, cache_path):
        try:
            with cache_path.open() as fh:
                data = json.load(fh)
            return ResumeProfile(
                **{k: v for k, v in data.items() if k in _PROFILE_FIELDS}
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("profile: cache load failed, re-parsing: %s", exc)

    from localjobscout.llm_backend import use_cli

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key and not use_cli():
        return None

    logger.info("profile: parsing resume via API → %s", cache_path)
    profile = _call_api(resume_text, api_key)
    if profile is None:
        return None

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("w") as fh:
            json.dump(asdict(profile), fh, indent=2)
    except Exception as exc:  # noqa: BLE001
        logger.debug("profile: could not write cache: %s", exc)

    return profile
