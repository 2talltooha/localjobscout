from __future__ import annotations

from pathlib import Path

import pytest

from localjobscout.master_resume import (
    MasterResume,
    MasterResumeError,
    load_master,
)

_GOOD = """\
contact:
  name: "Taha El Ghadi"
  location: "Waterloo, ON"
  email: "taha@example.com"
summaries:
  general: "Student who ships software."
  technical: "Builds Python tools."
items:
  - id: edu-guelph
    section: education
    core: true
    tags: [education, lab]
    content:
      title: "University of Guelph — B.Sc."
      sub: "2025 – Present"
      bullets:
        - "Hands-on wet-lab work in General Chemistry."
  - id: proj-localjobscout
    section: projects
    tags: [technical, ai]
    content:
      title: "LocalJobScout"
      bullets:
        - "Python CLI hardened to 265 tests."
"""


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "master.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_load_good_master_parses(tmp_path: Path) -> None:
    master = load_master(_write(tmp_path, _GOOD))
    assert isinstance(master, MasterResume)
    assert master.contact.name == "Taha El Ghadi"
    assert len(master.items) == 2
    assert master.summary_for("technical") == "Builds Python tools."
    # Unknown profile falls back to general.
    assert master.summary_for("nonexistent") == "Student who ships software."


def test_every_item_has_at_least_one_tag(tmp_path: Path) -> None:
    master = load_master(_write(tmp_path, _GOOD))
    for item in master.items:
        assert len(item.tags) >= 1


def test_item_with_no_tags_is_rejected(tmp_path: Path) -> None:
    bad = """\
contact:
  name: "Taha"
items:
  - id: x
    section: skills
    tags: []
    content:
      title: "Skills"
"""
    with pytest.raises(MasterResumeError):
        load_master(_write(tmp_path, bad))


def test_unknown_section_is_rejected(tmp_path: Path) -> None:
    bad = """\
contact:
  name: "Taha"
items:
  - id: x
    section: bogus-section
    tags: [technical]
    content:
      title: "Whatever"
"""
    with pytest.raises(MasterResumeError):
        load_master(_write(tmp_path, bad))


def test_missing_contact_name_is_rejected(tmp_path: Path) -> None:
    bad = """\
contact:
  location: "Waterloo, ON"
items:
  - id: x
    section: skills
    tags: [technical]
    content:
      title: "Skills"
"""
    with pytest.raises(MasterResumeError):
        load_master(_write(tmp_path, bad))


def test_duplicate_item_ids_rejected(tmp_path: Path) -> None:
    bad = """\
contact:
  name: "Taha"
items:
  - id: dup
    section: skills
    tags: [technical]
    content:
      title: "A"
  - id: dup
    section: projects
    tags: [technical]
    content:
      title: "B"
"""
    with pytest.raises(MasterResumeError):
        load_master(_write(tmp_path, bad))


def test_malformed_yaml_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(MasterResumeError):
        load_master(_write(tmp_path, "contact: [unclosed\n"))


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(MasterResumeError):
        load_master(tmp_path / "does-not-exist.yaml")


def test_items_for_tags_and_core(tmp_path: Path) -> None:
    master = load_master(_write(tmp_path, _GOOD))
    tech = master.items_for_tags({"technical"})
    assert [it.id for it in tech] == ["proj-localjobscout"]
    core = master.core_items()
    assert [it.id for it in core] == ["edu-guelph"]


def test_master_hash_stable_and_content_sensitive(tmp_path: Path) -> None:
    m1 = load_master(_write(tmp_path, _GOOD))
    m2 = load_master(_write(tmp_path, _GOOD))
    assert m1.master_hash() == m2.master_hash()
    changed = _GOOD.replace("265 tests", "300 tests")
    m3 = load_master(_write(tmp_path, changed))
    assert m3.master_hash() != m1.master_hash()


def test_all_text_includes_facts(tmp_path: Path) -> None:
    master = load_master(_write(tmp_path, _GOOD))
    text = master.all_text().lower()
    assert "wet-lab" in text
    assert "localjobscout" in text
    assert "taha el ghadi" in text


def test_real_master_yaml_is_valid() -> None:
    """The shipped data/resume/master.yaml must always pass validation."""
    path = Path("data/resume/master.yaml")
    if not path.exists():
        pytest.skip("real master.yaml not present")
    master = load_master(path)
    assert master.contact.name
    for item in master.items:
        assert item.tags
