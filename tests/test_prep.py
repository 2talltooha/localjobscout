from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from localjobscout import db as db_module
from localjobscout import prep as prep_module
from localjobscout.config import ScrapersConfig, Settings
from localjobscout.db import Job, make_job_id


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        resume_path=tmp_path / "resume.txt",
        db_path=tmp_path / "jobs.db",
        scrapers=ScrapersConfig(),
    )


def _job() -> Job:
    url = "https://example.com/lab"
    return Job(
        id=make_job_id("indeed", url),
        source="indeed",
        title="Lab Assistant",
        url=url,
        description="Help with sample prep.",
        company="Acme",
        location="Waterloo, ON",
        first_seen="2026-05-31T00:00:00+00:00",
    )


def _install_fake_anthropic(
    monkeypatch: pytest.MonkeyPatch, response_text: str
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


def test_generic_prep_without_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = _settings(tmp_path)
    settings.resume_path.write_text("Taha El Ghadi")
    db_module.init_db(settings.db_path)
    job = _job()
    db_module.upsert_job(job)

    path = prep_module.generate_and_save(job.id, settings=settings)
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "Interview Prep — Lab Assistant" in content
    assert "Q1" in content  # generic questions present


def test_anthropic_prep_with_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _install_fake_anthropic(
        monkeypatch, "## Q1: Why this role?\n**Suggested answer:** Because."
    )
    settings = _settings(tmp_path)
    settings.resume_path.write_text("Taha El Ghadi")
    db_module.init_db(settings.db_path)
    job = _job()
    db_module.upsert_job(job)

    path = prep_module.generate_and_save(job.id, settings=settings)
    content = path.read_text(encoding="utf-8")
    assert "Why this role?" in content


def test_prep_job_not_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = _settings(tmp_path)
    settings.resume_path.write_text("Taha")
    db_module.init_db(settings.db_path)
    with pytest.raises(prep_module.JobNotFoundError):
        prep_module.generate_and_save("deadbeef" * 8, settings=settings)


def test_prep_resume_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = _settings(tmp_path)  # resume not written
    db_module.init_db(settings.db_path)
    job = _job()
    db_module.upsert_job(job)
    with pytest.raises(FileNotFoundError):
        prep_module.generate_and_save(job.id, settings=settings)
