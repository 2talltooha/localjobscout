"""Fit & gap analysis — match a job's requirements against the master resume.

Given a parsed job description and the structured master resume, produce a
report that, for each key requirement, says whether the applicant's *real*
experience covers it (covered / partial / missing) and which master item is the
evidence. Also computes ATS keyword coverage: which exact job-description
keywords appear in qualifying master content.

The analysis runs through the Anthropic API (claude-haiku) and is cached in the
DB keyed on ``(job_id, master_hash)`` — a cache hit makes ZERO network calls,
and a changed master resume (new hash) transparently invalidates old reports.

HARD RULE: this module never invents coverage. The model is instructed to mark
a requirement ``missing`` whenever no master item genuinely supports it; the
report feeds tailoring, which may only re-emphasize facts that already exist.

Public API:
    analyze_and_cache(job, master, *, resume_text=None) -> GapReport | None
    GapReport.summary_line()    # compact "strengths / gaps" line for the queue
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from typing import Any

from localjobscout.db import Job
from localjobscout.master_resume import MasterResume

logger = logging.getLogger(__name__)

_VALID_STATUS = {"covered", "partial", "missing"}

_SYSTEM_PROMPT = (
    "You are an honest resume/job fit analyst. You assess whether an "
    "applicant's REAL, listed experience covers a job's requirements. You "
    "NEVER credit the applicant with experience that is not explicitly present "
    "in the provided master resume items. If no item genuinely supports a "
    "requirement, you mark it 'missing'. Output only valid JSON."
)

_JSON_INSTRUCTION = (
    "Respond with ONLY this JSON object (no markdown, no commentary):\n"
    "{\n"
    '  "requirements": [\n'
    '    {"requirement": "<short phrase from the JD>",\n'
    '     "status": "covered|partial|missing",\n'
    '     "covered_by": "<exact item id that is the evidence, or empty string>"}\n'
    "  ],\n"
    '  "ats_keywords": [\n'
    '    {"keyword": "<exact keyword from the JD>",\n'
    '     "present": true|false,\n'
    '     "where": "<item id where it appears, or empty string>"}\n'
    "  ],\n"
    '  "strengths": ["<short phrase: a requirement the applicant truly covers>"],\n'
    '  "gaps": ["<short phrase: a genuine requirement the applicant lacks>"]\n'
    "}\n\n"
    "Rules:\n"
    "- 'covered_by' and 'where' MUST be an exact item id from the list below, "
    "or an empty string. Never invent an id.\n"
    "- Mark 'covered' ONLY if a listed item plainly supports the requirement; "
    "'partial' for adjacent/transferable; 'missing' otherwise.\n"
    "- 'present' is true ONLY if the keyword actually appears in qualifying "
    "master content; do not credit keywords the applicant has not earned.\n"
    "- Cap each list at 8 items; pick the most decisive ones."
)


@dataclass
class Requirement:
    requirement: str
    status: str  # covered | partial | missing
    covered_by: str = ""


@dataclass
class KeywordCoverage:
    keyword: str
    present: bool
    where: str = ""


@dataclass
class GapReport:
    requirements: list[Requirement] = field(default_factory=list)
    ats_keywords: list[KeywordCoverage] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)

    # ── coverage helpers ────────────────────────────────────────────────
    def covered_item_ids(self) -> list[str]:
        """Master item ids cited as covering a requirement (deduped, ordered)."""
        seen: list[str] = []
        for req in self.requirements:
            if req.status in {"covered", "partial"} and req.covered_by:
                if req.covered_by not in seen:
                    seen.append(req.covered_by)
        return seen

    def ats_present_count(self) -> tuple[int, int]:
        """Return (present, total) ATS keyword counts."""
        total = len(self.ats_keywords)
        present = sum(1 for k in self.ats_keywords if k.present)
        return present, total

    def summary_line(self, max_each: int = 2) -> str:
        """Compact one-line summary: covered strengths + genuine gaps."""
        present, total = self.ats_present_count()
        strengths = self.strengths or [
            r.requirement for r in self.requirements if r.status == "covered"
        ]
        gaps = self.gaps or [
            r.requirement for r in self.requirements if r.status == "missing"
        ]
        parts: list[str] = []
        if strengths:
            parts.append("✓ " + "; ".join(strengths[:max_each]))
        if gaps:
            parts.append("gap: " + "; ".join(gaps[:max_each]))
        if total:
            parts.append(f"ATS {present}/{total}")
        return " · ".join(parts) if parts else "no analysis"

    # ── serialization ───────────────────────────────────────────────────
    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> GapReport:
        data = json.loads(raw)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GapReport:
        reqs = [
            Requirement(
                requirement=str(r.get("requirement", "")),
                status=(
                    str(r.get("status", "missing")).lower()
                    if str(r.get("status", "missing")).lower() in _VALID_STATUS
                    else "missing"
                ),
                covered_by=str(r.get("covered_by", "") or ""),
            )
            for r in data.get("requirements", [])
            if str(r.get("requirement", "")).strip()
        ]
        kws = [
            KeywordCoverage(
                keyword=str(k.get("keyword", "")),
                present=bool(k.get("present", False)),
                where=str(k.get("where", "") or ""),
            )
            for k in data.get("ats_keywords", [])
            if str(k.get("keyword", "")).strip()
        ]
        return cls(
            requirements=reqs,
            ats_keywords=kws,
            strengths=[str(s) for s in data.get("strengths", []) if str(s).strip()],
            gaps=[str(g) for g in data.get("gaps", []) if str(g).strip()],
        )


def _render_items(master: MasterResume) -> str:
    """Render master items as an id-labelled list for the prompt."""
    lines: list[str] = []
    for item in master.items:
        c = item.content
        body = c.title
        if c.sub:
            body += f" ({c.sub})"
        if c.bullets:
            body += " — " + " ".join(c.bullets)
        lines.append(f"[{item.id}] ({item.section}) {body}")
    return "\n".join(lines)


def _build_prompt(job: Job, master: MasterResume) -> str:
    return (
        "MASTER RESUME ITEMS (the applicant's only real experience; "
        "cite ids exactly):\n"
        f"{_render_items(master)}\n\n"
        "---\n"
        f"JOB TITLE: {job.title}\n"
        f"COMPANY: {job.company}\n"
        f"LOCATION: {job.location}\n"
        "JOB DESCRIPTION (first 3000 chars):\n"
        f"{(job.description or '')[:3000]}\n"
        "---\n\n"
        "Analyze fit between this job's key requirements and the applicant's "
        "real master items.\n\n"
        f"{_JSON_INSTRUCTION}"
    )


def _call_api(job: Job, master: MasterResume, api_key: str) -> GapReport | None:
    try:
        from localjobscout.llm_backend import make_client
        client: Any = make_client(api_key)
    except ImportError:
        return None

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=900,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_prompt(job, master)}],
        )
        raw = "".join(
            block.text for block in resp.content
            if getattr(block, "type", "") == "text"
        ).strip()
        return _parse_report(raw, job.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("gap analysis API failed for %s: %s", job.id[:8], exc)
        return None


def _parse_report(raw: str, job_id: str) -> GapReport | None:
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        logger.debug("gap: no JSON object in response for %s", job_id[:8])
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        logger.debug("gap: bad JSON for %s: %s", job_id[:8], exc)
        return None
    return GapReport.from_dict(data)


def analyze_and_cache(
    job: Job,
    master: MasterResume,
    *,
    resume_text: str | None = None,  # noqa: ARG001 — reserved for future fallback
) -> GapReport | None:
    """Return a gap report for *job* against *master*, caching in the DB.

    Cache key is ``(job.id, master.master_hash())``. A cache hit makes ZERO
    network calls. Returns None when no API backend is available and nothing is
    cached.
    """
    from localjobscout import db as db_module

    master_hash = master.master_hash()
    cached = db_module.get_gap_report(job.id, master_hash)
    if cached is not None:
        try:
            return GapReport.from_json(cached)
        except (json.JSONDecodeError, KeyError) as exc:
            logger.debug("gap: corrupt cache for %s, re-analyzing: %s", job.id[:8], exc)

    from localjobscout.llm_backend import use_cli

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key and not use_cli():
        return None

    report = _call_api(job, master, api_key)
    if report is None:
        return None

    db_module.set_gap_report(job.id, master_hash, report.to_json())
    return report
