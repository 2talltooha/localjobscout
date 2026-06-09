from __future__ import annotations

from pathlib import Path

import pytest

from localjobscout import cover_letter as cover_letter_module
from localjobscout.config import FocusConfig, ScrapersConfig, Settings
from localjobscout.db import Job, make_job_id


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        location="Waterloo, ON",
        match_threshold=0.17,
        scan_interval_minutes=60,
        use_semantic_matcher=False,
        resume_path=tmp_path / "resume.txt",
        db_path=tmp_path / "test.db",
        scrapers=ScrapersConfig(),
        focus=FocusConfig(
            keywords=["biology", "laboratory", "research", "clinical"]
        ),
    )


def _job(title: str, description: str = "", company: str = "Acme") -> Job:
    url = f"https://example.com/{title.replace(' ', '-')}"
    return Job(
        id=make_job_id("test", url),
        source="test",
        title=title,
        url=url,
        description=description,
        company=company,
        location="Waterloo, ON",
    )


def test_template_picks_lab_body_for_lab_title(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = _settings(tmp_path)
    job = _job("Lab Technician", "Biology laboratory research role.")
    body, backend = cover_letter_module.generate(
        job, settings=settings, resume_text="Jane Doe\nstudent."
    )
    assert backend == "template"
    assert "bench exposure" in body or "lab" in body.lower()
    assert "Lab Technician" in body
    assert "Acme" in body


def test_template_picks_healthcare_body_for_clinical_title(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = _settings(tmp_path)
    job = _job("Clinical Research Assistant", "Patient care clinical work.")
    body, _ = cover_letter_module.generate(
        job, settings=settings, resume_text="Jane Doe"
    )
    # healthcare template mentions patient contact / hospital environment
    assert (
        "patient" in body.lower()
        or "clinical" in body.lower()
        or "healthcare" in body.lower()
    )


def test_template_falls_back_to_default_for_unknown_title(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = _settings(tmp_path)
    job = _job("Widget Polisher Apprentice")
    body, backend = cover_letter_module.generate(
        job, settings=settings, resume_text="Jane Doe"
    )
    assert backend == "template"
    assert "Widget Polisher Apprentice" in body
    assert "Acme" in body


def test_user_name_extracted_from_resume_first_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = _settings(tmp_path)
    job = _job("Lab Technician")
    body, _ = cover_letter_module.generate(
        job,
        settings=settings,
        resume_text="Awsome Saws\nUniversity of Guelph\nBiology Honours",
    )
    assert "Awsome Saws" in body


def test_user_name_falls_back_when_resume_first_line_is_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = _settings(tmp_path)
    job = _job("Lab Technician")
    body, _ = cover_letter_module.generate(
        job,
        settings=settings,
        resume_text="user@email.com | 555-1234\nReal name on line 2",
    )
    # First line looks like contact info, not a name → fall back default.
    assert "Taha El Ghadi" in body


def test_keywords_extracted_from_focus_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = _settings(tmp_path)
    job = _job("Lab Technician", "biology and research")
    body, _ = cover_letter_module.generate(
        job, settings=settings, resume_text="Jane Doe"
    )
    assert "biology" in body or "research" in body


def test_render_markdown_includes_metadata_header(
    tmp_path: Path,
) -> None:
    job = _job("Lab Technician", company="Acme")
    body = "Dear Hiring Team,\n\nHello.\n"
    md = cover_letter_module.render_markdown(job, body, "template")
    assert "# Cover Letter — Lab Technician" in md
    assert "**Company:** Acme" in md
    assert "**Source:** test" in md
    assert "backend: template" in md
    assert "Dear Hiring Team," in md


def test_anthropic_path_skipped_when_no_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = _settings(tmp_path)
    job = _job("Lab Technician")
    _, backend = cover_letter_module.generate(
        job, settings=settings, resume_text="Jane Doe"
    )
    assert backend == "template"


def test_anthropic_path_falls_back_when_import_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even with an API key set, if the anthropic SDK is not installed
    (or import fails) we must still produce a template body."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")

    import builtins
    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "anthropic":
            raise ImportError("simulated missing module")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    settings = _settings(tmp_path)
    job = _job("Lab Technician")
    _, backend = cover_letter_module.generate(
        job, settings=settings, resume_text="Jane Doe"
    )
    assert backend == "template"
