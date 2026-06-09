from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from localjobscout import profile as profile_module
from localjobscout.profile import ResumeProfile, load_or_parse

_RESUME = """\
Taha El Ghadi
Waterloo, ON | tahaelghadi@gmail.com

First-year Biological Science (Honours) student at the University of Guelph.
Certifications: Standard First Aid and CPR.
Languages: English, Arabic, French.
Peer tutor, camp counselor.
"""

_PROFILE_JSON = json.dumps({
    "name": "Taha El Ghadi",
    "education": "1st year Biological Science, UoGuelph",
    "skills": ["peer tutoring", "camp counseling"],
    "certifications": ["Standard First Aid", "CPR"],
    "languages": ["English", "Arabic", "French"],
    "experience_level": "entry-level",
    "experience_summary": "Tutored peers and worked as a camp counselor.",
    "target_roles": ["lab assistant", "research assistant"],
    "avoid_roles": ["senior scientist", "lab manager"],
})


def _install_fake_anthropic(
    monkeypatch: pytest.MonkeyPatch,
    response_text: str,
) -> None:
    """Inject a fake `anthropic` module whose client returns response_text."""
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


def test_to_text_includes_all_fields() -> None:
    p = ResumeProfile(
        name="Jane Doe",
        education="2nd year CS",
        skills=["python"],
        certifications=["CPR"],
        languages=["English"],
        experience_summary="Did things.",
        target_roles=["intern"],
        avoid_roles=["CTO"],
    )
    text = p.to_text()
    assert "Jane Doe" in text
    assert "python" in text
    assert "CPR" in text
    assert "intern" in text
    assert "CTO" in text


def test_load_or_parse_returns_none_without_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    resume = tmp_path / "resume.txt"
    resume.write_text(_RESUME)
    cache = tmp_path / "profile.json"
    result = load_or_parse(resume, _RESUME, cache_path=cache)
    assert result is None
    assert not cache.exists()


def test_load_or_parse_calls_api_and_caches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _install_fake_anthropic(monkeypatch, _PROFILE_JSON)

    resume = tmp_path / "resume.txt"
    resume.write_text(_RESUME)
    cache = tmp_path / "profile.json"

    result = load_or_parse(resume, _RESUME, cache_path=cache)
    assert result is not None
    assert result.name == "Taha El Ghadi"
    assert "Arabic" in result.languages
    assert "lab assistant" in result.target_roles
    # Cache written to disk
    assert cache.exists()
    cached = json.loads(cache.read_text())
    assert cached["name"] == "Taha El Ghadi"


def test_load_or_parse_uses_cache_when_fresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    resume = tmp_path / "resume.txt"
    resume.write_text(_RESUME)
    cache = tmp_path / "profile.json"
    cache.write_text(_PROFILE_JSON)
    # Make cache newer than resume
    import os
    import time
    now = time.time()
    os.utime(resume, (now - 100, now - 100))
    os.utime(cache, (now, now))

    # No fake anthropic installed — if it tried to call API, import would
    # still succeed but we assert it never builds a client by checking the
    # cached value comes straight through.
    def _boom(*a: Any, **k: Any) -> None:
        raise AssertionError("API should not be called when cache is fresh")

    monkeypatch.setattr(profile_module, "_call_api", _boom)

    result = load_or_parse(resume, _RESUME, cache_path=cache)
    assert result is not None
    assert result.name == "Taha El Ghadi"


def test_load_or_parse_reparse_when_resume_newer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _install_fake_anthropic(monkeypatch, _PROFILE_JSON)

    resume = tmp_path / "resume.txt"
    resume.write_text(_RESUME)
    cache = tmp_path / "profile.json"
    cache.write_text(json.dumps({"name": "STALE"}))

    import os
    import time
    now = time.time()
    # Resume newer than cache → must re-parse
    os.utime(cache, (now - 100, now - 100))
    os.utime(resume, (now, now))

    result = load_or_parse(resume, _RESUME, cache_path=cache)
    assert result is not None
    assert result.name == "Taha El Ghadi"  # fresh value, not STALE


def test_load_or_parse_handles_bad_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _install_fake_anthropic(monkeypatch, "not valid json at all")

    resume = tmp_path / "resume.txt"
    resume.write_text(_RESUME)
    cache = tmp_path / "profile.json"

    result = load_or_parse(resume, _RESUME, cache_path=cache)
    assert result is None


def test_load_or_parse_strips_markdown_fences(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    fenced = f"```json\n{_PROFILE_JSON}\n```"
    _install_fake_anthropic(monkeypatch, fenced)

    resume = tmp_path / "resume.txt"
    resume.write_text(_RESUME)
    cache = tmp_path / "profile.json"

    result = load_or_parse(resume, _RESUME, cache_path=cache)
    assert result is not None
    assert result.name == "Taha El Ghadi"
