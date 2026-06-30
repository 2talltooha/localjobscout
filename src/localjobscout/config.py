from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from localjobscout.prefilter import PrefilterRules

_DEFAULT_FOCUS_KEYWORDS: list[str] = [
    "research", "lab", "laboratory", "pharmacy", "clinical",
    "biology", "health", "patient", "tutor", "premed",
    "science", "assistant", "entry level",
]


class JobCategory(BaseModel):
    """A target job category — a tunable bucket of search queries + keywords.

    ``queries`` are the search strings fed to query-capable scrapers (one
    scraper run per query → more daily volume). ``keywords`` feed scoring's
    target-fit / focus boost. ``enabled`` lets a category be toggled off
    without deleting it.

    NB: code-level defaults intentionally ship with EMPTY ``queries`` so that
    bare ``Settings()`` (used widely in tests) keeps single-instance scraper
    behaviour. The shipped ``config.yaml`` supplies real queries to widen
    intake in production.
    """
    name: str
    enabled: bool = True
    keywords: list[str] = []
    queries: list[str] = []
    # Cover-letter framing (Step 3): a one-line domain framing injected into the
    # letter prompt/template so a dev/retail/admin role gets a domain-appropriate
    # letter — not a clinical one. ``forbidden_claims`` are category-specific
    # phrases layered ON TOP of the universal baseline during validation (e.g.
    # police clinical/patient claims on a retail letter, not on a lab one).
    letter_framing: str = ""
    forbidden_claims: list[str] = []


# Cross-domain default categories. Keywords are populated (for scoring/focus);
# queries are left empty on purpose (see JobCategory docstring) — config.yaml
# fills them in to drive the volume widening.
_DEFAULT_CATEGORIES: list[JobCategory] = [
    JobCategory(
        name="general_dev",
        keywords=[
            "software", "developer", "programmer", "engineer", "python",
            "javascript", "web", "it support", "help desk",
            "technical support", "data", "qa", "junior developer",
        ],
        letter_framing=(
            "building practical technical skills, learning tools quickly, and "
            "approaching problems methodically"
        ),
        # Dev-domain fabrications. NOT policed for clinical/licence — those are
        # lab_research's concern; this category just gets the universal baseline
        # plus these.
        forbidden_claims=[
            "years of professional software development",
            "computer science degree",
        ],
    ),
    JobCategory(
        name="lab_research",
        keywords=[
            "research assistant", "lab", "laboratory", "research", "science",
            "biology", "chemistry", "clinical", "specimen", "technician",
        ],
        letter_framing=(
            "scientific coursework, careful protocol-following, and accurate "
            "record-keeping"
        ),
        # The clinical/licence policing the old premed validator did lives here.
        # "patient care" (not just "...experience") catches "patient care
        # principles" and any other patient-care phrasing.
        forbidden_claims=[
            "clinical experience", "patient care",
            "registered nurse", "professional licence",
        ],
    ),
    JobCategory(
        name="retail_service",
        keywords=[
            "retail", "customer service", "cashier", "sales associate",
            "barista", "server", "food service", "store", "stock",
            "front desk",
        ],
        letter_framing=(
            "reliability, teamwork, and clear communication with customers"
        ),
        # Retail-domain fabrication only — deliberately NO clinical/licence.
        forbidden_claims=[
            "years of retail management experience",
        ],
    ),
    JobCategory(
        name="admin",
        keywords=[
            "administrative", "receptionist", "office", "data entry",
            "clerk", "scheduling", "coordinator", "filing",
        ],
        letter_framing=(
            "organization, attention to detail, and dependable communication"
        ),
        # Admin-domain fabrication only — deliberately NO clinical/licence.
        forbidden_claims=[
            "years of administrative experience",
        ],
    ),
]


def _keyword_matches(keyword: str, text: str) -> bool:
    """Whole-word/phrase match — NOT substring. Avoids 'lab' matching inside
    'collaborate'/'available' or 'data' inside 'database', which mis-routed jobs
    (a BDO tax co-op → lab_research, a hospital scheduler → general_dev)."""
    return re.search(rf"\b{re.escape(keyword)}\b", text) is not None


def classify_category(
    title: str, description: str, categories: list[JobCategory]
) -> JobCategory | None:
    """Best-matching enabled category for a job, or None (unmatched).

    Classification is TITLE-GATED: a category only qualifies if at least one of
    its keywords matches the job TITLE as a whole word. The title is the real
    signal — a BDO 'Credits & Incentives' tax co-op whose description merely
    mentions 'research'/'science' (SR&ED) has no lab keyword in its title, so it
    stays UNMATCHED instead of mis-routing to lab_research. Among title-matched
    categories, description hits break ties (title weighted 2×). Unmatched jobs
    fall back to the neutral default letter, never a premed one."""
    title_l = (title or "").lower()
    desc_l = (description or "").lower()
    best: JobCategory | None = None
    best_score = 0.0
    for cat in categories:
        if not cat.enabled:
            continue
        title_hits = sum(
            1 for kw in cat.keywords if _keyword_matches(kw.lower(), title_l)
        )
        if title_hits == 0:
            continue  # title-gated: no title signal → not a candidate
        desc_hits = sum(
            1 for kw in cat.keywords if _keyword_matches(kw.lower(), desc_l)
        )
        score = 2.0 * title_hits + desc_hits
        if score > best_score:
            best_score = score
            best = cat
    return best


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


# Suitability "stance" text injected into the LLM prompt per active profile.
# Describes the TARGETING stance (how picky), not applicant facts — those come
# from the resume/ResumeProfile. Pulling this out of suitability.py is what
# lets the same codebase run picky (biomed) or take-anything (broad).
_BIOMED_PERSONA = (
    "The applicant is NOT enrolled in any professional program (pharmacy, "
    "nursing, paramedicine, lab technology) and cannot hold regulated-profession "
    "registration (e.g. Registered Pharmacy Technician, RN, MLT). Judge fit for "
    "a pre-medicine pathway: clinical, patient-contact, health, biology/research, "
    "and care work are ideal. If the posting requires a completed diploma/degree, "
    "professional registration, or program enrolment the applicant does not have, "
    "score 0.2 or lower regardless of subject-matter match."
)
_BROAD_PERSONA = (
    "The applicant is an entry-level student seeking part-time, summer, or "
    "flexible income work across MANY fields — general/office, retail and "
    "customer service, administrative, entry tech/IT, and lab/research all "
    "count. Do NOT penalize a role for being outside health or science. Judge "
    "realistic fit for an entry-level worker. Only mark unqualified when the "
    "posting demands a credential, professional licence, or years of experience "
    "the applicant lacks."
)


class TargetProfile(BaseModel):
    """A target-fit profile. Selects the scoring blend weights, which category
    keywords define target-fit, the suitability stance, and whether the
    pre-med domain relevance gate applies.

    Two ship by default: ``broad`` (income / take-anything) and ``biomed``
    (picky pre-med). ``active_profile`` on Settings selects one.
    """
    name: str
    # Blend weights: combined = w_resume*resume_fit + w_target*target_fit.
    w_resume: float = 0.6
    w_target: float = 0.4
    # Category names whose keywords define target-fit. Empty = all enabled.
    target_categories: list[str] = []
    # LLM suitability stance text (see _BIOMED_PERSONA / _BROAD_PERSONA).
    suitability_persona: str = ""
    # "premed" applies the pre-med domain relevance gate (drops dev/retail/
    # trades/sales as off-narrative); "none" disables it (take-anything).
    relevance_gate: str = "none"
    # Optional per-profile notification/queue threshold override.
    match_threshold: float | None = None


_DEFAULT_PROFILES: list[TargetProfile] = [
    TargetProfile(
        name="broad",
        w_resume=0.6,
        w_target=0.4,
        target_categories=[],          # all enabled categories
        suitability_persona=_BROAD_PERSONA,
        relevance_gate="none",
    ),
    TargetProfile(
        name="biomed",
        w_resume=0.4,
        w_target=0.6,
        target_categories=["lab_research"],
        suitability_persona=_BIOMED_PERSONA,
        relevance_gate="premed",
        match_threshold=0.22,
    ),
]


class FocusConfig(BaseModel):
    keywords: list[str] = Field(default_factory=lambda: list(_DEFAULT_FOCUS_KEYWORDS))
    boost_per_hit: float = 0.05
    max_boost: float = 0.25
    title_boost_multiplier: float = 2.0
    expand_resume: bool = False
    expand_weight: int = 3


class BlendConfig(BaseModel):
    """Resolved scoring blend handed to the matcher.

    ``combined = w_resume*resume_fit + w_target*target_fit``, where
    ``target_fit`` is a 0–1 keyword-hit score over ``target_keywords`` (title
    hits weighted by ``title_boost_multiplier``, saturated at
    ``target_saturation`` total hits). When ``enabled`` is False the matcher
    falls back to the legacy focus-boost path (used by direct-construction unit
    tests).
    """
    enabled: bool = False
    w_resume: float = 0.6
    w_target: float = 0.4
    target_keywords: list[str] = []
    title_boost_multiplier: float = 2.0
    target_saturation: float = 6.0


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


class CoworkConfig(BaseModel):
    enabled: bool = False
    queue_dir: Path = Path("cowork_queue")
    # Minimum combined score to include in queue (defaults to match_threshold
    # when 0.0; set explicitly to override).
    min_score: float = 0.0
    # When True, only export jobs with qualification_verdict != 'no'.
    gate_qualified: bool = True


class FetchConfig(BaseModel):
    """Page-fetch engine settings (Phase 1: engine selection only).

    The fetch adapter (``scrapers/fetcher.py``) routes HTML fetches through
    Scrapling when ``enabled`` and the library is installed, otherwise the
    pipeline falls back to the built-in httpx/Playwright path. Selectors are
    untouched in Phase 1 — this only swaps the transport.
    """

    # Master switch. When False (or Scrapling not installed), every fetch uses
    # the legacy httpx/Playwright path — identical to pre-integration behaviour.
    enabled: bool = True
    # Engine when a source has no explicit choice:
    #   auto   → stealth for sources in stealth_sources, plain otherwise
    #   plain  → Scrapling Fetcher (fast HTTP)
    #   stealth→ Scrapling StealthyFetcher (real browser, beats Cloudflare)
    default_engine: Literal["auto", "plain", "stealth"] = "auto"
    # Sources forced onto StealthyFetcher in auto mode (anti-bot / Cloudflare).
    stealth_sources: list[str] = Field(
        default_factory=lambda: ["indeed", "linkedin"]
    )
    # Per-source engine overrides, e.g. {"talent": "stealth"}. Wins over auto.
    source_engines: dict[str, Literal["plain", "stealth"]] = Field(
        default_factory=dict
    )
    # Sources that always use the built-in httpx/Playwright path, never
    # Scrapling. Use for JSON-API sources where Scrapling's HTML wrapping
    # breaks parsing (e.g. remoteok returns raw JSON that Scrapling wraps in
    # <html><body><p>…</p>).
    legacy_sources: list[str] = Field(
        default_factory=lambda: ["remoteok"]
    )
    # Fetch timeout (seconds) handed to the Scrapling engine.
    timeout: float = 20.0


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
    # Target job categories — tunable buckets of queries + keywords. Drives
    # both intake widening (per-query scraper runs) and scoring target-fit.
    categories: list[JobCategory] = Field(
        default_factory=lambda: [c.model_copy(deep=True) for c in _DEFAULT_CATEGORIES]
    )
    # Safety cap on how many distinct category queries are fanned out to each
    # query-capable scraper per scan (volume vs. request-count trade-off).
    max_intake_queries: int = 8
    # Target-fit profiles + the active selector. The active profile drives the
    # scoring blend weights, target-fit keywords, suitability stance, and the
    # pre-med relevance gate. Ship broad (income) + biomed (picky).
    profiles: list[TargetProfile] = Field(
        default_factory=lambda: [p.model_copy(deep=True) for p in _DEFAULT_PROFILES]
    )
    active_profile: str = "broad"
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
    cowork: CoworkConfig = Field(default_factory=CoworkConfig)
    fetch: FetchConfig = Field(default_factory=FetchConfig)

    def category_queries(self) -> list[str]:
        """Deduped, order-preserving search queries from enabled categories.

        Capped at ``max_intake_queries``. Empty when no enabled category
        defines a query (the code-default case) — callers then fall back to
        each scraper's own ``query``.
        """
        seen: set[str] = set()
        out: list[str] = []
        for cat in self.categories:
            if not cat.enabled:
                continue
            for q in cat.queries:
                key = q.strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    out.append(q.strip())
        return out[: max(0, self.max_intake_queries)]

    def category_keywords(self) -> list[str]:
        """Deduped, order-preserving keywords from enabled categories."""
        seen: set[str] = set()
        out: list[str] = []
        for cat in self.categories:
            if not cat.enabled:
                continue
            for kw in cat.keywords:
                key = kw.strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    out.append(kw.strip())
        return out

    def active_target_profile(self) -> TargetProfile:
        """The selected target profile (by name), else the first defined, else
        a synthesized broad default."""
        for prof in self.profiles:
            if prof.name == self.active_profile:
                return prof
        if self.profiles:
            return self.profiles[0]
        return TargetProfile(name="broad", suitability_persona=_BROAD_PERSONA)

    def target_keywords(self) -> list[str]:
        """Keywords defining target-fit for the active profile: keywords of the
        profile's ``target_categories`` (or all enabled categories when empty),
        deduped and order-preserving."""
        prof = self.active_target_profile()
        if prof.target_categories:
            wanted = {n.lower() for n in prof.target_categories}
            cats = [
                c for c in self.categories
                if c.enabled and c.name.lower() in wanted
            ]
        else:
            cats = [c for c in self.categories if c.enabled]
        seen: set[str] = set()
        out: list[str] = []
        for cat in cats:
            for kw in cat.keywords:
                key = kw.strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    out.append(kw.strip())
        return out

    def build_blend(self) -> BlendConfig:
        """Resolve the active profile + categories into a matcher BlendConfig."""
        prof = self.active_target_profile()
        return BlendConfig(
            enabled=True,
            w_resume=prof.w_resume,
            w_target=prof.w_target,
            target_keywords=self.target_keywords(),
            title_boost_multiplier=self.focus.title_boost_multiplier,
        )

    def effective_threshold(self) -> float:
        """Match threshold, overridden by the active profile when it sets one."""
        prof = self.active_target_profile()
        return prof.match_threshold if prof.match_threshold is not None \
            else self.match_threshold

    def queries_for(self, board_query: str) -> list[str]:
        """Search queries to fan out to one query-capable scraper.

        Returns the enabled-category queries when any exist, else a single
        list holding the scraper's own configured ``board_query`` (preserving
        the pre-categories single-instance behaviour).
        """
        cat_qs = self.category_queries()
        return cat_qs if cat_qs else [board_query]

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
