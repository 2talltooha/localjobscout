from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from localjobscout.db import Job, make_job_id
from localjobscout.profile import ResumeProfile
from localjobscout.tailor import (
    TailoringError,
    generate_tailoring,
    render_markdown,
    save_tailoring,
)

_SUGGESTIONS = """\
## Keywords to Add
- "patient interaction" — fits your camp counselor experience.

## Sections to Emphasize
- Lead with tutoring.

## Honest Reframings
- "camp counselor" → "supervised groups of 20+ children".

## Gaps (Do Not Fabricate)
- No phlebotomy certification.

## Verdict
Solid entry-level fit. Add patient-interaction language.
"""


def _job(title: str = "Lab Assistant") -> Job:
    url = f"https://example.com/{title.replace(' ', '-')}"
    return Job(
        id=make_job_id("indeed", url),
        source="indeed",
        title=title,
        url=url,
        description="Assist with sample prep and patient intake.",
        company="Acme Health",
        location="Waterloo, ON",
    )


def _profile() -> ResumeProfile:
    return ResumeProfile(
        name="Taha El Ghadi",
        education="1st year Biology, UoGuelph",
        skills=["peer tutoring"],
        certifications=["CPR"],
        languages=["English", "Arabic", "French"],
        target_roles=["lab assistant"],
        avoid_roles=["lab manager"],
    )


def _install_fake_anthropic(
    monkeypatch: pytest.MonkeyPatch,
    response_text: str,
) -> None:
    fake = types.ModuleType("anthropic")

    class _Block:
        type = "text"
        text = response_text

    class _Resp:
        content = [_Block()]

    class _Messages:
        def create(self, **kwargs: Any) -> _Resp:
            return _Resp()

    class _Client:
        def __init__(self, **kwargs: Any) -> None:
            self.messages = _Messages()

    fake.Anthropic = _Client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake)


def test_generate_tailoring_raises_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(TailoringError, match="ANTHROPIC_API_KEY"):
        generate_tailoring(_job(), "resume text", _profile())


def test_generate_tailoring_returns_suggestions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _install_fake_anthropic(monkeypatch, _SUGGESTIONS)
    result = generate_tailoring(_job(), "resume text", _profile())
    assert "Keywords to Add" in result
    assert "Honest Reframings" in result
    assert "Verdict" in result


def test_generate_tailoring_works_without_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _install_fake_anthropic(monkeypatch, _SUGGESTIONS)
    result = generate_tailoring(_job(), "raw resume text here", None)
    assert "Keywords to Add" in result


def test_generate_tailoring_raises_on_empty_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _install_fake_anthropic(monkeypatch, "")
    with pytest.raises(TailoringError, match="empty"):
        generate_tailoring(_job(), "resume", _profile())


def test_render_markdown_includes_header() -> None:
    md = render_markdown(_job("Research Assistant"), _SUGGESTIONS)
    assert "# Resume Tailoring — Research Assistant" in md
    assert "**Company:** Acme Health" in md
    assert "**Source:** indeed" in md
    assert "Keywords to Add" in md


def test_save_tailoring_writes_file(tmp_path: Path) -> None:
    job = _job("Lab Assistant")
    path = save_tailoring(job, _SUGGESTIONS, tmp_path)
    assert path.exists()
    assert path.name == f"indeed-{job.id[:8]}-resume-tips.md"
    content = path.read_text(encoding="utf-8")
    assert "Resume Tailoring" in content
    assert "Verdict" in content


def test_save_tailoring_creates_dir(tmp_path: Path) -> None:
    nested = tmp_path / "applications"
    job = _job()
    path = save_tailoring(job, _SUGGESTIONS, nested)
    assert nested.exists()
    assert path.parent == nested
