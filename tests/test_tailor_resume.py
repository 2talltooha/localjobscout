from __future__ import annotations

from pathlib import Path

import pytest

from localjobscout import tailor_resume as tr
from localjobscout.config import Settings
from localjobscout.db import Job, make_job_id
from localjobscout.master_resume import MasterResume, load_master

_MASTER_YAML = """\
contact:
  name: "Taha El Ghadi"
  location: "Waterloo, ON"
  email: "taha@example.com"
summaries:
  general: "Student who ships software and works with people."
  technical: "Builds Python and TypeScript tools."
  research: "Hands-on wet-lab work in General Chemistry."
  customer-service: "Reliable trilingual camp counselor with customer service."
items:
  - id: edu-guelph
    section: education
    core: true
    tags: [education, lab, research, science]
    content:
      title: "University of Guelph — B.Sc. Biological Sciences"
      sub: "2025 – Present"
      bullets:
        - "Hands-on wet-lab work in General Chemistry I & II."
  - id: certs
    section: certs
    core: true
    tags: [certs]
    content:
      title: "Certifications"
      bullets:
        - "First Aid & CPR, Babysitting Certification."
  - id: skills-languages
    section: skills
    core: true
    tags: [languages]
    content:
      title: "Languages"
      bullets:
        - "English, Arabic (fluent), French (fluent)."
  - id: proj-openclaw
    section: projects
    tags: [technical, ai, leadership]
    content:
      title: "OpenClaw Hack Toronto"
      sub: "Market Master Award, 2026"
      bullets:
        - "Built an autonomous AI agent in TypeScript."
  - id: skills-technical
    section: skills
    tags: [technical, ai]
    content:
      title: "Technical"
      bullets:
        - "Python, TypeScript, Git, LLM APIs."
  - id: exp-camp
    section: experience
    tags: [customer-service, leadership]
    content:
      title: "Camp Counselor — Tayba Elementary"
      sub: "2022 – 2023"
      bullets:
        - "Supervised groups of 10–20 children."
        - "Communicated with parents and staff; resolved conflicts."
  - id: skills-workplace
    section: skills
    tags: [customer-service]
    content:
      title: "Workplace"
      bullets:
        - "Customer service, cash handling, inventory & stocking."
"""


@pytest.fixture
def master(tmp_path: Path) -> MasterResume:
    p = tmp_path / "master.yaml"
    p.write_text(_MASTER_YAML, encoding="utf-8")
    return load_master(p)


@pytest.fixture
def settings() -> Settings:
    return Settings()


def _job(title: str, description: str = "") -> Job:
    url = f"https://example.com/{title.replace(' ', '-')}"
    return Job(
        id=make_job_id("indeed", url),
        source="indeed",
        title=title,
        url=url,
        description=description or title,
        company="Acme",
        location="Waterloo, ON",
    )


# ── classification ──────────────────────────────────────────────────────────


def test_classify_research() -> None:
    s = Settings()
    job = _job("Research Lab Assistant", "wet lab chemistry biology research")
    assert tr.classify_profile(job, s) == "research"


def test_classify_technical() -> None:
    s = Settings()
    job = _job("Software Developer", "python typescript backend engineer coding")
    assert tr.classify_profile(job, s) == "technical"


def test_classify_customer_service() -> None:
    s = Settings()
    job = _job("Retail Cashier", "customer service sales store front desk")
    assert tr.classify_profile(job, s) == "customer-service"


def test_classify_general_fallback() -> None:
    s = Settings()
    job = _job("General Helper", "miscellaneous duties")
    assert tr.classify_profile(job, s) == "general"


# ── selection / dropping ────────────────────────────────────────────────────


def _all_item_ids(resume: tr.TailoredResume) -> set[str]:
    return {it.id for _s, items in resume.sections for it in items}


def test_customer_service_drops_technical_and_lab_items(
    master: MasterResume, settings: Settings
) -> None:
    job = _job("Retail Associate", "customer service cashier sales store")
    resume = tr.build(job, master, settings, profile="customer-service")
    ids = _all_item_ids(resume)
    # No coding/lab items on a customer-service resume.
    assert "proj-openclaw" not in ids
    assert "skills-technical" not in ids
    # Real customer-service + core items remain.
    assert "exp-camp" in ids
    assert "skills-workplace" in ids
    assert "certs" in ids  # core always kept
    # And no item carrying a dropped tag slipped through.
    for _s, items in resume.sections:
        for it in items:
            if not it.core:
                assert not ({"technical", "ai", "lab", "research"} & set(it.tags))


def test_research_leads_with_education_then_projects(
    master: MasterResume, settings: Settings
) -> None:
    job = _job("Research Assistant", "research lab chemistry biology wet lab")
    resume = tr.build(job, master, settings, profile="research")
    section_order = [s for s, _items in resume.sections]
    assert section_order[0] == "education"
    assert "projects" in section_order


def test_summary_variant_matches_profile(
    master: MasterResume, settings: Settings
) -> None:
    job = _job("Software Developer", "python coding")
    resume = tr.build(job, master, settings, profile="technical")
    assert resume.summary == "Builds Python and TypeScript tools."


def test_bullets_capped_per_section(master: MasterResume) -> None:
    s = Settings()
    s.tailor.max_bullets_per_section = 1
    job = _job("Camp Counselor", "customer service children")
    resume = tr.build(job, master, s, profile="customer-service")
    for _section, items in resume.sections:
        for it in items:
            assert len(it.content.bullets) <= 1


# ── validation ──────────────────────────────────────────────────────────────


def test_clean_build_passes_validation(
    master: MasterResume, settings: Settings
) -> None:
    for prof in ("research", "technical", "customer-service", "general"):
        job = _job("Some Role")
        resume = tr.build(job, master, settings, profile=prof)
        warnings = tr.validate_tailored(resume, master)
        assert warnings == [], f"{prof}: {warnings}"


def test_off_master_claim_is_rejected(
    master: MasterResume, settings: Settings
) -> None:
    job = _job("Some Role")
    resume = tr.build(job, master, settings, profile="general")
    # Inject a fabricated bullet into the first section's first item.
    section, items = resume.sections[0]
    items[0].content.bullets.append("Managed a team of 50 engineers at Google.")
    warnings = tr.validate_tailored(resume, master)
    assert any("untraceable" in w for w in warnings)


def test_off_master_summary_is_rejected(
    master: MasterResume, settings: Settings
) -> None:
    job = _job("Some Role")
    resume = tr.build(job, master, settings, profile="general")
    resume.summary = "I have ten years of professional surgical experience."
    warnings = tr.validate_tailored(resume, master)
    assert any("summary" in w or "off-master" in w for w in warnings)


def test_forbidden_claims_applied(master: MasterResume) -> None:
    s = Settings()
    s.cover_letter.forbidden_claims = ["wet-lab"]
    job = _job("Research Assistant", "research lab")
    resume = tr.build(job, master, s, profile="research")
    warnings = tr.validate_tailored(
        resume, master, extra_forbidden=s.cover_letter.forbidden_claims
    )
    # The research summary/education bullet legitimately mentions wet-lab, but
    # an explicit forbidden_claim still surfaces it for review.
    assert any("wet-lab" in w for w in warnings)


# ── rendering ───────────────────────────────────────────────────────────────


def test_render_pdf_is_single_page(
    master: MasterResume, settings: Settings, tmp_path: Path
) -> None:
    pytest.importorskip("pypdf")
    job = _job("Research Assistant", "research lab chemistry")
    resume = tr.build(job, master, settings, profile="research")
    out = tmp_path / "resume.pdf"
    tr.render_pdf(resume, out)
    assert out.exists() and out.stat().st_size > 0

    import pypdf

    reader = pypdf.PdfReader(str(out))
    assert len(reader.pages) == 1


def test_save_writes_md_and_pdf(
    master: MasterResume, settings: Settings, tmp_path: Path
) -> None:
    job = _job("Research Assistant", "research lab")
    resume = tr.build(job, master, settings, profile="research")
    paths = tr.save(job, resume, tmp_path)
    assert paths["markdown"].exists()
    assert paths["pdf"].exists()
    assert paths["pdf"].parent.name == f"indeed-{job.id[:8]}"


def test_render_markdown_contains_name_and_profile(
    master: MasterResume, settings: Settings
) -> None:
    job = _job("Software Developer", "python")
    resume = tr.build(job, master, settings, profile="technical")
    md = tr.render_markdown(resume)
    assert "Taha El Ghadi" in md
    assert "technical" in md
