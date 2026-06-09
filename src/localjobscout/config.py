from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from localjobscout.prefilter import PrefilterRules

_DEFAULT_FOCUS_KEYWORDS: list[str] = [
    "research", "lab", "laboratory", "pharmacy", "clinical",
    "biology", "health", "patient", "tutor", "premed",
    "science", "assistant", "entry level",
]


class ScraperConfig(BaseModel):
    enabled: bool = True
    max_pages: int = 3
    query: str = ""
    locations: list[str] = []


class ICIMSSite(BaseModel):
    """One hospital/employer on the iCIMS ATS. Find `subdomain` by opening the
    employer's careers page and reading the iCIMS link
    (e.g. encareers-cmh.icims.com → subdomain "encareers-cmh")."""
    enabled: bool = True
    name: str                 # unique source id, e.g. "cambridge"
    subdomain: str            # e.g. "encareers-cmh"
    company: str              # display name
    city: str                 # location city (province assumed ON)
    max_pages: int = 3


class ScrapersConfig(BaseModel):
    jobbank: ScraperConfig = Field(default_factory=ScraperConfig)
    remoteok: ScraperConfig = Field(default_factory=ScraperConfig)
    adzuna: ScraperConfig = Field(default_factory=ScraperConfig)
    linkedin: ScraperConfig = Field(default_factory=ScraperConfig)
    indeed: ScraperConfig = Field(default_factory=ScraperConfig)
    uoguelph: ScraperConfig = Field(default_factory=ScraperConfig)
    uwaterloo: ScraperConfig = Field(default_factory=ScraperConfig)
    conestoga: ScraperConfig = Field(default_factory=ScraperConfig)
    laurier: ScraperConfig = Field(default_factory=ScraperConfig)
    hamiltonhealth: ScraperConfig = Field(
        default_factory=lambda: ScraperConfig(enabled=False)
    )
    grandriver: ScraperConfig = Field(
        default_factory=lambda: ScraperConfig(enabled=False)
    )
    stmarys: ScraperConfig = Field(
        default_factory=lambda: ScraperConfig(enabled=False)
    )
    cambridge: ScraperConfig = Field(
        default_factory=lambda: ScraperConfig(enabled=False)
    )
    talent: ScraperConfig = Field(
        default_factory=lambda: ScraperConfig(enabled=False)
    )
    # Hospital/employer iCIMS portals (server-rendered, httpx-scrapable).
    icims_sites: list[ICIMSSite] = []


class FocusConfig(BaseModel):
    keywords: list[str] = Field(default_factory=lambda: list(_DEFAULT_FOCUS_KEYWORDS))
    boost_per_hit: float = 0.05
    max_boost: float = 0.25
    title_boost_multiplier: float = 2.0
    expand_resume: bool = False
    expand_weight: int = 3


class SalaryConfig(BaseModel):
    # When enabled, jobs that advertise pay at/above target_annual get a score
    # boost (up to `boost`). Jobs with no salary listed are unaffected.
    enabled: bool = False
    target_annual: int = 35_000  # annualized; full boost at/above this
    boost: float = 0.10          # max score boost added


class GeoConfig(BaseModel):
    # Soft proximity boost: jobs whose location contains one of `home_cities`
    # get `boost` added to their score, surfacing hometown roles without
    # excluding anywhere else. Empty list / 0 boost = disabled.
    home_cities: list[str] = []
    boost: float = 0.0
    # Hard commute filter for the manual queue: drop jobs whose location
    # contains any of these substrings (too far to commute). Empty = no filter.
    exclude_locations: list[str] = []


class MatchingConfig(BaseModel):
    min_salary: int | None = None
    max_salary: int | None = None
    required_skills: list[str] = []
    excluded_skills: list[str] = []
    allowed_job_types: list[str] = []
    excluded_keywords: list[str] = []


class AlertConfig(BaseModel):
    enabled: bool = False
    method: str = "email"
    email_smtp_host: str = "smtp.gmail.com"
    email_smtp_port: int = 587
    email_from: str = ""
    email_to: list[str] = []
    email_password: str = ""
    min_matches_to_alert: int = 1


class CoverLetterConfig(BaseModel):
    # Phrases that validate() always flags regardless of resume content.
    # Add/remove entries in config.yaml without touching code.
    # Example: forbidden_claims: ["strong research background", "prior lab work"]
    forbidden_claims: list[str] = []


class ResumeConfig(BaseModel):
    # Structured master resume (single source of truth for tailoring) and the
    # per-profile tag weights used when selecting items for a tailored resume.
    master_path: Path = Path("data/resume/master.yaml")
    profiles: dict[str, dict[str, float]] = Field(default_factory=dict)


class TailorConfig(BaseModel):
    # Daily-flow auto-tailoring: build gap analysis + tailored resume for the
    # top_n manual-queue matches, capping bullets per section on output.
    auto: bool = True        # auto-tailor top_n matches at the end of each scan
    top_n: int = 5
    max_bullets_per_section: int = 4


class AutoApplyConfig(BaseModel):
    enabled: bool = False
    from_email: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_password: str = ""
    min_score: float = 0.22
    daily_limit: int = 10
    unattended: bool = False  # if True + --auto-apply-send, skip per-job review


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    location: str = "waterloo, ON"
    match_threshold: float = 0.22
    scan_interval_minutes: int = 60
    use_semantic_matcher: bool = False
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    resume_path: Path = Path("data/resume.txt")
    # Extra resume files for A/B comparison via --compare-resumes. The primary
    # resume_path is always included automatically.
    resume_variants: list[Path] = []
    db_path: Path = Path("data/jobs.db")
    # Manual queue freshness: hide jobs older than this many days (by posted_at,
    # falling back to first_seen) and jobs whose listed deadline has passed.
    # Set to 0 to disable the age cap (deadline filtering still applies).
    queue_max_age_days: int = 30
    # Sources to hide from --manual-queue (e.g. "indeed" — Cloudflare blocks
    # liveness/full-text checks, so its postings can't be verified open).
    queue_exclude_sources: list[str] = []
    # Run suitability scoring inline during each scan (requires API key).
    # Caps to inline_suitability_limit jobs per scan to keep scans fast.
    inline_suitability: bool = True
    inline_suitability_limit: int = 5
    scrapers: ScrapersConfig = Field(default_factory=ScrapersConfig)
    prefilter: PrefilterRules = Field(default_factory=PrefilterRules)
    focus: FocusConfig = Field(default_factory=FocusConfig)
    salary: SalaryConfig = Field(default_factory=SalaryConfig)
    geo: GeoConfig = Field(default_factory=GeoConfig)
    matching: MatchingConfig = Field(default_factory=MatchingConfig)
    alerts: AlertConfig = Field(default_factory=AlertConfig)
    auto_apply: AutoApplyConfig = Field(default_factory=AutoApplyConfig)
    cover_letter: CoverLetterConfig = Field(default_factory=CoverLetterConfig)
    resume: ResumeConfig = Field(default_factory=ResumeConfig)
    tailor: TailorConfig = Field(default_factory=TailorConfig)

    @classmethod
    def load(cls, yaml_path: Path = Path("config.yaml")) -> Settings:
        """Load settings from config.yaml, then overlay any env vars / .env."""
        data: dict[str, Any] = {}
        if yaml_path.exists():
            with yaml_path.open() as fh:
                loaded = yaml.safe_load(fh)
                if isinstance(loaded, dict):
                    data = loaded
        # Overlay the auto-apply SMTP secret from the environment / .env so it
        # never has to live in the git-tracked config.yaml. Env wins over yaml.
        smtp_pw = os.environ.get("AUTO_APPLY_SMTP_PASSWORD")
        if not smtp_pw:
            env_file = Path(".env")
            if env_file.exists():
                for line in env_file.read_text().splitlines():
                    line = line.strip()
                    if line.startswith("AUTO_APPLY_SMTP_PASSWORD") and "=" in line:
                        smtp_pw = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        if smtp_pw:
            aa = data.get("auto_apply")
            if not isinstance(aa, dict):
                aa = {}
            aa["smtp_password"] = smtp_pw
            data["auto_apply"] = aa
        return cls(**data)
