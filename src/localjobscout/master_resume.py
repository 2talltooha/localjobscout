"""Master resume — the single structured source of truth for tailoring.

``data/resume/master.yaml`` holds the applicant's real, complete resume as
structured data: core contact info, per-profile summary variants, and a flat
list of taggable items (experience, projects, education, skills, activities,
certs).

Downstream tailoring (Phase 3) may only *select, reorder, reword, and
re-emphasize* facts that already live in this file — it may never introduce a
new employer, skill, claim, date, or number. This module is the gatekeeper:
``load_master`` parses and validates, failing loudly on anything malformed so a
bad master can never silently feed the tailoring pipeline.

Public API:
    load_master(path) -> MasterResume      # parse + validate, raises on bad
    MasterResume.master_hash()             # stable content hash (cache key)
    MasterResume.all_text()                # flat text for claim validation
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

VALID_SECTIONS = frozenset(
    {"experience", "projects", "education", "skills", "activities", "certs"}
)


class MasterResumeError(Exception):
    """Raised when the master resume file is missing or malformed."""


class Contact(BaseModel):
    model_config = {"extra": "forbid"}

    name: str
    location: str = ""
    phone: str = ""
    email: str = ""

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("contact.name must not be blank")
        return v


class ItemContent(BaseModel):
    model_config = {"extra": "forbid"}

    title: str
    sub: str = ""
    bullets: list[str] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("item content.title must not be blank")
        return v


class ResumeItem(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    section: str
    content: ItemContent
    tags: list[str] = Field(min_length=1)
    core: bool = False

    @field_validator("id")
    @classmethod
    def _id_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("item id must not be blank")
        return v

    @field_validator("section")
    @classmethod
    def _section_known(cls, v: str) -> str:
        if v not in VALID_SECTIONS:
            raise ValueError(
                f"unknown section {v!r}; valid: {sorted(VALID_SECTIONS)}"
            )
        return v

    @field_validator("tags")
    @classmethod
    def _tags_non_empty_strings(cls, v: list[str]) -> list[str]:
        cleaned = [t.strip() for t in v if t.strip()]
        if not cleaned:
            raise ValueError("every item must have at least one non-blank tag")
        return cleaned


class MasterResume(BaseModel):
    model_config = {"extra": "forbid"}

    contact: Contact
    summaries: dict[str, str] = Field(default_factory=dict)
    items: list[ResumeItem] = Field(min_length=1)

    @field_validator("items")
    @classmethod
    def _unique_ids(cls, v: list[ResumeItem]) -> list[ResumeItem]:
        ids = [item.id for item in v]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate item ids: {sorted(dupes)}")
        return v

    def items_for_tags(self, tags: set[str]) -> list[ResumeItem]:
        """Return items tagged with any of *tags*, preserving file order."""
        return [it for it in self.items if tags.intersection(it.tags)]

    def core_items(self) -> list[ResumeItem]:
        """Return always-include items, preserving file order."""
        return [it for it in self.items if it.core]

    def summary_for(self, profile: str) -> str:
        """Return the summary variant for *profile*, falling back to general."""
        return self.summaries.get(profile) or self.summaries.get("general", "")

    def all_text(self) -> str:
        """Flatten every fact into one lowercase-friendly text blob.

        Used as the reference corpus when validating that a generated resume
        introduces no claim absent from the master (same contract the cover
        letter validator uses against the raw resume text).
        """
        parts: list[str] = [
            self.contact.name,
            self.contact.location,
            self.contact.email,
        ]
        parts.extend(self.summaries.values())
        for item in self.items:
            parts.append(item.content.title)
            parts.append(item.content.sub)
            parts.extend(item.content.bullets)
        return "\n".join(p for p in parts if p)

    def master_hash(self) -> str:
        """Stable SHA-256 of the master content (cache key for gap analysis)."""
        canonical = self.model_dump_json()
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_master(path: Path) -> MasterResume:
    """Parse and validate the master resume YAML at *path*.

    Raises MasterResumeError on a missing file, malformed YAML, or any schema
    violation — never returns a partially-valid object.
    """
    if not path.exists():
        raise MasterResumeError(
            f"Master resume not found at {path}. "
            "Create data/resume/master.yaml (see master_resume.py docstring)."
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise MasterResumeError(f"Master resume YAML is invalid: {exc}") from exc

    if not isinstance(raw, dict):
        raise MasterResumeError(
            f"Master resume must be a YAML mapping, got {type(raw).__name__}."
        )

    try:
        return MasterResume.model_validate(raw)
    except ValidationError as exc:
        raise MasterResumeError(
            f"Master resume failed schema validation:\n{exc}"
        ) from exc
