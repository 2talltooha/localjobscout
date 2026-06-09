from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from localjobscout import db as db_module
from localjobscout import suitability as suit_module
from localjobscout.db import Job, make_job_id
from localjobscout.profile import ResumeProfile


def _job(title: str = "Lab Assistant") -> Job:
    url = f"https://example.com/{title.replace(' ', '-')}"
    return Job(
        id=make_job_id("indeed", url),
        source="indeed",
        title=title,
        url=url,
        description="Entry-level lab support role.",
        company="Acme",
        location="Waterloo, ON",
        first_seen="2026-05-31T00:00:00+00:00",
    )


def _profile() -> ResumeProfile:
    return ResumeProfile(name="Taha", target_roles=["lab assistant"])


class _Recorder:
    """Captures kwargs passed to messages.create across the test."""

    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.calls: list[dict[str, Any]] = []


def _install_fake_anthropic(
    monkeypatch: pytest.MonkeyPatch,
    recorder: _Recorder,
) -> None:
    fake = types.ModuleType("anthropic")

    class _Block:
        type = "text"

        def __init__(self, text: str) -> None:
            self.text = text

    class _Resp:
        def __init__(self, text: str) -> None:
            self.content = [_Block(text)]

    class _Messages:
        def __init__(self, rec: _Recorder) -> None:
            self._rec = rec

        def create(self, **kwargs: Any) -> _Resp:
            self._rec.calls.append(kwargs)
            return _Resp(self._rec.response_text)

    class _Client:
        def __init__(self, **kwargs: Any) -> None:
            self.messages = _Messages(recorder)

    fake.Anthropic = _Client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake)


@pytest.fixture
def _db(tmp_path: Path) -> Path:
    db_path = tmp_path / "jobs.db"
    db_module.init_db(db_path)
    return db_path


def test_returns_none_without_key(
    _db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    job = _job()
    db_module.upsert_job(job)
    assert suit_module.score_and_cache(job, "resume") is None


def test_scores_and_caches(
    _db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    rec = _Recorder('{"suitability": 0.8, "reason": "good entry-level fit"}')
    _install_fake_anthropic(monkeypatch, rec)

    job = _job()
    db_module.upsert_job(job)

    result = suit_module.score_and_cache(job, "resume text")
    assert result is not None
    score, reason = result
    assert score == 0.8
    assert "entry-level" in reason
    # Cached in DB
    assert db_module.get_suitability(job.id) == (0.8, "good entry-level fit")


def test_cache_hit_skips_api(
    _db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    rec = _Recorder('{"suitability": 0.9, "reason": "should not be used"}')
    _install_fake_anthropic(monkeypatch, rec)

    job = _job()
    db_module.upsert_job(job)
    db_module.set_suitability(job.id, 0.5, "pre-cached")

    result = suit_module.score_and_cache(job, "resume")
    assert result == (0.5, "pre-cached")
    assert rec.calls == []  # API never called


def test_profile_path_uses_prompt_caching(
    _db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    rec = _Recorder('{"suitability": 0.7, "reason": "ok"}')
    _install_fake_anthropic(monkeypatch, rec)

    job = _job()
    db_module.upsert_job(job)

    result = suit_module.score_and_cache(job, "resume", profile=_profile())
    assert result is not None
    assert len(rec.calls) == 1
    # The cached path sends a list-of-blocks content with cache_control set
    content = rec.calls[0]["messages"][0]["content"]
    assert isinstance(content, list)
    assert content[0]["cache_control"] == {"type": "ephemeral"}
    # Profile text must appear in the cached block
    assert "APPLICANT PROFILE" in content[0]["text"]


def test_plain_path_uses_string_content(
    _db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    rec = _Recorder('{"suitability": 0.6, "reason": "ok"}')
    _install_fake_anthropic(monkeypatch, rec)

    job = _job()
    db_module.upsert_job(job)

    suit_module.score_and_cache(job, "resume text", profile=None)
    content = rec.calls[0]["messages"][0]["content"]
    assert isinstance(content, str)  # plain prompt, no caching blocks


def test_clamps_out_of_range_score(
    _db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    rec = _Recorder('{"suitability": 1.9, "reason": "over"}')
    _install_fake_anthropic(monkeypatch, rec)

    job = _job()
    db_module.upsert_job(job)
    result = suit_module.score_and_cache(job, "resume")
    assert result is not None
    assert result[0] == 1.0  # clamped to max
