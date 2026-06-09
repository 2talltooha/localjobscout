"""Per-job resume tailoring — build a job-specific resume from the master.

Given a job and the structured master resume, this module:

1. classifies the job into a profile (research / technical / customer-service /
   general) from its title + description,
2. selects and orders master items by tag relevance to that profile, leading
   with strengths the gap analysis confirms are covered,
3. drops sections irrelevant to the profile (a customer-service resume carries
   no coding/lab items; a research resume leads with lab + projects),
4. picks the matching summary variant, and
5. enforces a one-page cap on render.

NOTHING is fabricated. Items are copied from the master verbatim — selection,
ordering, and bullet-capping only. Before any file is written, the rendered
resume is validated against the master (every line must trace back to a master
fact) and against the cover-letter validator + configured forbidden_claims; a
resume that fails is rejected, never saved.

Public API:
    classify_profile(job, settings) -> str
    build(job, master, settings, *, gap=None) -> TailoredResume
    validate_tailored(resume, master, extra_forbidden) -> list[str]
    render_markdown(resume) -> str
    render_pdf(resume, out_path) -> Path
    save(job, resume, out_dir) -> dict[str, Path]   # writes .md + .pdf
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from localjobscout.config import Settings
from localjobscout.db import Job
from localjobscout.master_resume import Contact, MasterResume, ResumeItem

if TYPE_CHECKING:
    from localjobscout.gap import GapReport

PROFILES = ("research", "technical", "customer-service", "general")

# ── profile classification ──────────────────────────────────────────────────
# Keyword sets matched against the job title + description. First profile with
# the most hits wins; ties broken by the PROFILES order (research first).
_PROFILE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "research": (
        "research", "lab", "laboratory", "clinical", "biology", "biological",
        "chemistry", "scientist", "wet lab", "assay", "pcr", "specimen",
        "pharmacy", "patient", "health", "science",
    ),
    "technical": (
        "software", "developer", "python", "javascript", "typescript",
        "engineer", "programming", "data", "machine learning", "ai", "coding",
        "full stack", "backend", "frontend", "git",
    ),
    "customer-service": (
        "customer", "retail", "cashier", "sales", "service", "front desk",
        "reception", "barista", "host", "store", "client", "hospitality",
        "child", "childcare", "child care", "camp", "counselor", "youth",
        "daycare", "recreation",
    ),
}

# Per-profile tag weights (item ordering) and hard-drop tags (exclusion).
# resume.profiles in config.yaml overlays the weights; drop tags stay built-in.
_DEFAULT_WEIGHTS: dict[str, dict[str, float]] = {
    "research": {
        "lab": 3, "research": 3, "science": 2, "premed": 2, "education": 2,
        "technical": 1, "ai": 1, "leadership": 1, "languages": 1, "certs": 1,
        "customer-service": 0.5, "tutor": 1,
    },
    "technical": {
        "technical": 3, "ai": 3, "leadership": 1.5, "education": 1,
        "languages": 1, "certs": 0.5, "lab": 0.5, "research": 0.5,
    },
    "customer-service": {
        "customer-service": 3, "leadership": 2, "languages": 2, "certs": 1,
        "education": 1, "tutor": 1.5,
    },
    "general": {
        "leadership": 2, "customer-service": 1.5, "technical": 1.5, "ai": 1,
        "education": 1.5, "lab": 1, "research": 1, "languages": 1, "certs": 1,
        "tutor": 1, "science": 1,
    },
}

_DROP_TAGS: dict[str, frozenset[str]] = {
    # A customer-service resume carries no coding/lab items.
    "customer-service": frozenset({"technical", "ai", "lab", "research"}),
    "research": frozenset(),
    "technical": frozenset(),
    "general": frozenset(),
}

# Section print order per profile (sections absent from the selection are
# silently skipped).
_SECTION_ORDER: dict[str, tuple[str, ...]] = {
    "research": (
        "education", "projects", "experience", "activities", "skills", "certs",
    ),
    "technical": (
        "projects", "skills", "education", "experience", "activities", "certs",
    ),
    "customer-service": (
        "experience", "activities", "skills", "education", "certs",
    ),
    "general": (
        "education", "experience", "projects", "activities", "skills", "certs",
    ),
}

_SECTION_TITLES: dict[str, str] = {
    "education": "Education",
    "projects": "Projects & Awards",
    "experience": "Experience",
    "activities": "Leadership & Activities",
    "skills": "Skills",
    "certs": "Certifications",
}

# Big bonus so gap-confirmed strengths lead their section.
_GAP_BONUS = 10.0
# Keep non-core item count bounded so the result stays one page.
_MAX_NONCORE_ITEMS = 7


@dataclass
class TailoredResume:
    profile: str
    contact: Contact
    summary: str
    # Ordered (section_key, [items]) — items already ordered + bullet-capped.
    sections: list[tuple[str, list[ResumeItem]]] = field(default_factory=list)

    def iter_lines(self) -> list[str]:
        """Every human-visible text line, for validation + plain rendering."""
        lines: list[str] = [self.contact.name]
        contact_bits = [self.contact.location, self.contact.phone, self.contact.email]
        lines.append("  ".join(b for b in contact_bits if b))
        if self.summary:
            lines.append(self.summary)
        for _section, items in self.sections:
            for item in items:
                lines.append(item.content.title)
                if item.content.sub:
                    lines.append(item.content.sub)
                lines.extend(item.content.bullets)
        return lines


# ── classification ──────────────────────────────────────────────────────────


# Whole-word matchers per keyword (so "ai" never matches "available", etc.).
_PROFILE_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    profile: [re.compile(rf"\b{re.escape(kw)}\b") for kw in kws]
    for profile, kws in _PROFILE_KEYWORDS.items()
}


def classify_profile(job: Job, settings: Settings) -> str:  # noqa: ARG001
    """Classify a job into one of PROFILES from its title + description."""
    blob = f"{job.title} {job.description or ''}".lower()
    # Title hits weigh double — the title is the strongest signal.
    title_blob = job.title.lower()
    best = "general"
    best_score = 0.0
    for profile in ("research", "technical", "customer-service"):
        pats = _PROFILE_PATTERNS[profile]
        score = float(sum(len(p.findall(blob)) for p in pats))
        score += sum(2 for p in pats if p.search(title_blob))
        if score > best_score:
            best_score = score
            best = profile
    return best if best_score > 0 else "general"


# ── selection ───────────────────────────────────────────────────────────────


def _weights_for(profile: str, settings: Settings) -> dict[str, float]:
    weights = dict(_DEFAULT_WEIGHTS.get(profile, _DEFAULT_WEIGHTS["general"]))
    overlay = settings.resume.profiles.get(profile)
    if overlay:
        weights.update({k: float(v) for k, v in overlay.items()})
    return weights


def _item_score(
    item: ResumeItem,
    weights: dict[str, float],
    covered_ids: set[str],
) -> float:
    score = sum(weights.get(tag, 0.0) for tag in item.tags)
    if item.id in covered_ids:
        score += _GAP_BONUS
    return score


def build(
    job: Job,
    master: MasterResume,
    settings: Settings,
    *,
    gap: GapReport | None = None,
    profile: str | None = None,
) -> TailoredResume:
    """Build a tailored resume for *job* from *master*. Does not write anything.

    *gap* may be a GapReport (Phase 2) whose covered items are promoted to lead
    their sections. *profile* overrides automatic classification.
    """
    prof = profile or classify_profile(job, settings)
    if prof not in PROFILES:
        prof = "general"
    weights = _weights_for(prof, settings)
    drop = _DROP_TAGS.get(prof, frozenset())

    covered_ids: set[str] = set(gap.covered_item_ids()) if gap is not None else set()

    # 1. Select: keep core always; drop non-core items carrying a drop tag.
    selected: list[ResumeItem] = []
    for item in master.items:
        if item.core:
            selected.append(item)
            continue
        if drop & set(item.tags):
            continue
        if _item_score(item, weights, covered_ids) <= 0:
            continue
        selected.append(item)

    # 2. Cap non-core items by score to keep the resume to one page.
    noncore = [it for it in selected if not it.core]
    noncore.sort(
        key=lambda it: _item_score(it, weights, covered_ids), reverse=True
    )
    keep_noncore = set(it.id for it in noncore[:_MAX_NONCORE_ITEMS])
    selected = [it for it in selected if it.core or it.id in keep_noncore]

    # 3. Group by section, order within section (gap-covered + score first),
    #    cap bullets per section, then emit sections in profile order.
    cap = max(1, settings.tailor.max_bullets_per_section)
    by_section: dict[str, list[ResumeItem]] = {}
    for item in selected:
        by_section.setdefault(item.section, []).append(item)

    sections: list[tuple[str, list[ResumeItem]]] = []
    section_order = _SECTION_ORDER.get(prof, _SECTION_ORDER["general"])
    for section in section_order:
        items = by_section.get(section)
        if not items:
            continue
        items.sort(
            key=lambda it: _item_score(it, weights, covered_ids), reverse=True
        )
        capped = [
            ResumeItem(
                id=it.id,
                section=it.section,
                content=it.content.model_copy(
                    update={"bullets": it.content.bullets[:cap]}
                ),
                tags=it.tags,
                core=it.core,
            )
            for it in items
        ]
        sections.append((section, capped))

    return TailoredResume(
        profile=prof,
        contact=master.contact,
        summary=master.summary_for(prof),
        sections=sections,
    )


# ── validation ──────────────────────────────────────────────────────────────


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def validate_tailored(
    resume: TailoredResume,
    master: MasterResume,
    extra_forbidden: list[str] | None = None,
) -> list[str]:
    """Return warnings for any line not traceable to the master, plus the
    cover-letter validator's findings. Empty list = safe to save.
    """
    from localjobscout.cover_letter import validate as cl_validate

    warnings: list[str] = []

    # 1. Traceability: every bullet/title/sub must exist verbatim in the
    #    master, and the summary must be one of the master's summary variants.
    master_lines = {
        _normalize(item.content.title) for item in master.items
    }
    master_lines |= {
        _normalize(item.content.sub) for item in master.items if item.content.sub
    }
    for item in master.items:
        master_lines |= {_normalize(b) for b in item.content.bullets}
    master_summaries = {_normalize(s) for s in master.summaries.values()}

    if resume.summary and _normalize(resume.summary) not in master_summaries:
        warnings.append("summary is not a master summary variant (off-master)")

    for _section, items in resume.sections:
        for item in items:
            if _normalize(item.content.title) not in master_lines:
                warnings.append(f"untraceable title: {item.content.title!r}")
            if item.content.sub and _normalize(item.content.sub) not in master_lines:
                warnings.append(f"untraceable sub: {item.content.sub!r}")
            for bullet in item.content.bullets:
                if _normalize(bullet) not in master_lines:
                    warnings.append(f"untraceable bullet: {bullet[:50]!r}")

    # 2. Reuse the cover-letter validator against the master's full text so
    #    forbidden_claims and fabrication patterns apply the same way.
    resume_text = "\n".join(resume.iter_lines())
    warnings.extend(
        cl_validate(resume_text, master.all_text(), extra_forbidden=extra_forbidden)
    )
    return warnings


# ── rendering ───────────────────────────────────────────────────────────────


def render_markdown(resume: TailoredResume) -> str:
    """Render the tailored resume as Markdown (for preview + audit trail)."""
    c = resume.contact
    out: list[str] = [f"# {c.name}"]
    contact_bits = [c.location, c.phone, c.email]
    out.append(" · ".join(b for b in contact_bits if b))
    out.append(f"\n_Profile: {resume.profile}_\n")
    if resume.summary:
        out.append(resume.summary + "\n")
    for section, items in resume.sections:
        out.append(f"## {_SECTION_TITLES.get(section, section.title())}")
        for item in items:
            line = f"**{item.content.title}**"
            if item.content.sub:
                line += f" — {item.content.sub}"
            out.append(line)
            for bullet in item.content.bullets:
                out.append(f"- {bullet}")
            out.append("")
    return "\n".join(out).rstrip() + "\n"


# Map common unicode punctuation to latin-1 so fpdf2's core fonts render it.
_ASCII_MAP = {
    "—": "-", "–": "-", "•": "-", "’": "'",
    "‘": "'", "“": '"', "”": '"', "·": "-",
    "…": "...", " ": " ",
}


def _ascii(text: str) -> str:
    for src, dst in _ASCII_MAP.items():
        text = text.replace(src, dst)
    return text.encode("latin-1", "replace").decode("latin-1")


def render_pdf(resume: TailoredResume, out_path: Path) -> Path:
    """Render the tailored resume to a single-page PDF at *out_path*.

    Auto page-break is disabled and a vertical budget is enforced so the output
    is guaranteed to be exactly one page; content that would overflow is
    dropped rather than spilling onto a second page.
    """
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    pdf = FPDF(format="letter", unit="mm")
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()
    margin = 15.0
    pdf.set_margins(margin, margin, margin)
    width = pdf.w - 2 * margin
    max_y = pdf.h - margin

    def room(height: float) -> bool:
        return pdf.get_y() + height <= max_y

    c = resume.contact
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 9, _ascii(c.name), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 9)
    contact_bits = [c.location, c.phone, c.email]
    pdf.cell(
        0, 5, _ascii("  |  ".join(b for b in contact_bits if b)),
        new_x=XPos.LMARGIN, new_y=YPos.NEXT,
    )
    pdf.ln(1)

    if resume.summary and room(16):
        pdf.set_font("Helvetica", "I", 9)
        pdf.multi_cell(width, 4.2, _ascii(resume.summary))
        pdf.ln(1.5)

    for section, items in resume.sections:
        title = _SECTION_TITLES.get(section, section.title())
        if not room(10):
            break
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_draw_color(150, 150, 150)
        pdf.cell(
            0, 6, _ascii(title.upper()), border="B",
            new_x=XPos.LMARGIN, new_y=YPos.NEXT,
        )
        pdf.ln(0.5)
        for item in items:
            if not room(8):
                break
            pdf.set_font("Helvetica", "B", 10)
            pdf.multi_cell(width, 4.6, _ascii(item.content.title))
            if item.content.sub:
                pdf.set_font("Helvetica", "I", 8.5)
                pdf.multi_cell(width, 4.0, _ascii(item.content.sub))
            pdf.set_font("Helvetica", "", 9)
            for bullet in item.content.bullets:
                if not room(5):
                    break
                pdf.multi_cell(width, 4.2, _ascii(f"-  {bullet}"))
            pdf.ln(1.2)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(out_path))
    return out_path


def save(job: Job, resume: TailoredResume, out_dir: Path) -> dict[str, Path]:
    """Write the tailored resume as both .md and .pdf under
    ``<out_dir>/<source>-<id8>/``. Returns the written paths.
    """
    job_dir = out_dir / f"{job.source}-{job.id[:8]}"
    job_dir.mkdir(parents=True, exist_ok=True)
    md_path = job_dir / "resume.md"
    md_path.write_text(render_markdown(resume), encoding="utf-8")
    pdf_path = render_pdf(resume, job_dir / "resume.pdf")
    return {"markdown": md_path, "pdf": pdf_path}
