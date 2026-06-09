from __future__ import annotations

from pathlib import Path

from localjobscout.config import SalaryConfig, Settings


def test_defaults_when_no_file(tmp_path: Path) -> None:
    settings = Settings.load(tmp_path / "missing.yaml")
    assert settings.location == "waterloo, ON"
    assert settings.match_threshold == 0.22
    assert settings.inline_suitability is True
    assert settings.inline_suitability_limit == 5
    assert settings.salary.enabled is False
    assert settings.resume_variants == []


def test_load_overrides_from_yaml(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "location: 'Guelph, ON'\n"
        "match_threshold: 0.15\n"
        "inline_suitability: false\n"
        "salary:\n"
        "  enabled: true\n"
        "  target_annual: 50000\n"
        "  boost: 0.2\n"
        "resume_variants:\n"
        "  - data/resume-clinical.txt\n"
        "  - data/resume-research.txt\n"
    )
    settings = Settings.load(cfg)
    assert settings.location == "Guelph, ON"
    assert settings.match_threshold == 0.15
    assert settings.inline_suitability is False
    assert settings.salary.enabled is True
    assert settings.salary.target_annual == 50000
    assert settings.salary.boost == 0.2
    assert len(settings.resume_variants) == 2
    assert settings.resume_variants[0] == Path("data/resume-clinical.txt")


def test_scraper_defaults_present() -> None:
    settings = Settings()
    assert settings.scrapers.jobbank.enabled is True
    assert settings.scrapers.hamiltonhealth.enabled is False
    assert settings.scrapers.cambridge.enabled is False


def test_salary_config_defaults() -> None:
    s = SalaryConfig()
    assert s.enabled is False
    assert s.target_annual == 35_000
    assert s.boost == 0.10


def test_focus_defaults() -> None:
    settings = Settings()
    assert "research" in settings.focus.keywords
    assert settings.focus.boost_per_hit == 0.05
    assert settings.focus.max_boost == 0.25


def test_malformed_yaml_falls_back_to_defaults(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("- this is a list not a dict\n")
    settings = Settings.load(cfg)
    # non-dict YAML is ignored → defaults
    assert settings.location == "waterloo, ON"
