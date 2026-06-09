from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from localjobscout import db as db_module
from localjobscout import gap as gap_module
from localjobscout.db import Job, make_job_id
from localjobscout.master_resume import MasterResume, load_master

_MASTER_YAML = """\
contact:
  name: "Taha El Ghadi"
summaries:
  general: "Student who ships software."
items:
  - id: skills-technical
    section: skills
    tags: [technical]
    content:
      title: "Technical"
      bullets: ["Python, TypeScript, Git."]
  - id: edu-guelph
    section: education
    core: true
    tags: [education, lab]
    content:
      title: "University of Guelph — B.Sc."
      bullets: ["Wet-lab work in General Chemistry."]
"""


def _master(tmp_path: Path) -> MasterResume:
    p = tmp_path / "master.yaml"
    p.write_text(_MASTER_YAML, encoding="utf-8")
    return load_master(p)


def _job() -> Job:
    url = "https://example.com/rn"
    return Job(
        id=make_job_id("indeed", url),
        source="indeed",
        title="Registered Nurse",
        url=url,
        description=(
        "Requires active RN licence and 5 years ICU experience. Python a plus."
    ),
        company="Hospital",
        location="Waterloo, ON",
        first_seen="2026-06-01T00:00:00+00:00",
    )


class _Recorder:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.calls: list[dict[str, Any]] = []


def _install_fake_anthropic(
    monkeypatch: pytest.MonkeyPatch, recorder: _Recorder
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
        def create(self, **kwargs: Any) -> _Resp:
            recorder.calls.append(kwargs)
            return _Resp(recorder.response_text)

    class _Client:
        def __init__(self, **kwargs: Any) -> None:
            self.messages = _Messages()

    fake.Anthropic = _Client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake)


_RESPONSE = json.dumps(
    {
        "requirements": [
            {
                "requirement": "Python",
                "status": "covered",
                "covered_by": "skills-technical",
            },
            {"requirement": "RN licence", "status": "missing", "covered_by": ""},
            {
                "requirement": "5 years ICU experience",
                "status": "missing",
                "covered_by": "",
            },
        ],
        "ats_keywords": [
            {"keyword": "Python", "present": True, "where": "skills-technical"},
            {"keyword": "ICU", "present": False, "where": ""},
        ],
        "strengths": ["Python"],
        "gaps": ["RN licence", "ICU experience"],
    }
)


@pytest.fixture
def _db(tmp_path: Path) -> Path:
    db_path = tmp_path / "jobs.db"
    db_module.init_db(db_path)
    return db_path


def test_returns_none_without_key(
    _db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LOCALJOBSCOUT_USE_CLI", raising=False)
    job = _job()
    db_module.upsert_job(job)
    assert gap_module.analyze_and_cache(job, _master(tmp_path)) is None


def test_missing_requirement_reported_missing_not_hallucinated(
    _db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _install_fake_anthropic(monkeypatch, _Recorder(_RESPONSE))

    job = _job()
    db_module.upsert_job(job)
    report = gap_module.analyze_and_cache(job, _master(tmp_path))
    assert report is not None

    by_req = {r.requirement: r for r in report.requirements}
    # A requirement the applicant clearly does not meet stays "missing".
    assert by_req["RN licence"].status == "missing"
    assert by_req["RN licence"].covered_by == ""
    # A genuinely covered requirement cites a real master item id.
    assert by_req["Python"].status == "covered"
    assert by_req["Python"].covered_by == "skills-technical"


def test_cache_hit_triggers_zero_llm_calls(
    _db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    rec = _Recorder(_RESPONSE)
    _install_fake_anthropic(monkeypatch, rec)

    job = _job()
    db_module.upsert_job(job)
    master = _master(tmp_path)

    # First call populates the cache (one API call).
    first = gap_module.analyze_and_cache(job, master)
    assert first is not None
    assert len(rec.calls) == 1

    # Second call must hit the cache and make ZERO further calls.
    second = gap_module.analyze_and_cache(job, master)
    assert second is not None
    assert len(rec.calls) == 1  # unchanged
    assert second.summary_line() == first.summary_line()


def test_changed_master_hash_invalidates_cache(
    _db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    rec = _Recorder(_RESPONSE)
    _install_fake_anthropic(monkeypatch, rec)

    job = _job()
    db_module.upsert_job(job)

    gap_module.analyze_and_cache(job, _master(tmp_path))
    assert len(rec.calls) == 1

    # A different master (new hash) must miss the cache → new API call.
    changed = tmp_path / "m2.yaml"
    changed.write_text(_MASTER_YAML.replace("Git.", "Git, Docker."), encoding="utf-8")
    gap_module.analyze_and_cache(job, load_master(changed))
    assert len(rec.calls) == 2


def test_summary_line_compact() -> None:
    report = gap_module.GapReport.from_json(_RESPONSE)
    line = report.summary_line()
    assert "Python" in line
    assert "gap:" in line
    assert "ATS 1/2" in line


def test_covered_item_ids() -> None:
    report = gap_module.GapReport.from_json(_RESPONSE)
    assert report.covered_item_ids() == ["skills-technical"]


def test_unknown_status_coerced_to_missing() -> None:
    report = gap_module.GapReport.from_dict(
        {"requirements": [{"requirement": "X", "status": "bogus", "covered_by": ""}]}
    )
    assert report.requirements[0].status == "missing"


def test_parse_report_tolerates_surrounding_text() -> None:
    raw = "Here is the analysis:\n" + _RESPONSE + "\nThanks!"
    report = gap_module._parse_report(raw, "abc123")
    assert report is not None
    assert any(r.requirement == "Python" for r in report.requirements)
