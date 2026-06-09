"""Pre-med relevance gate.

The whole point of LocalJobScout is to surface jobs that *strengthen a medical
school application* — clinical exposure, patient contact, health/biology
research, care work, and health-adjacent science. Keyword matching alone keeps
dragging in domain-mismatched roles (a "Laboratory Technician" at a civil
engineering firm testing concrete; an "Entry Level Sales Representative") that
share vocabulary but do nothing for a pre-med narrative.

`premed_relevance(title, description)` decides whether a posting belongs:

1. Hard-exclude clearly non-medical domains (engineering, trades, industrial,
   software, finance, generic sales) even when they mention "lab"/"research".
2. Otherwise require at least one pre-med-positive signal — clinical, patient,
   health, biology/chemistry, care work, or service/leadership that reads well
   for med-school (camp counsellor, crisis line, etc.).

Kept deliberately broad on the positive side: per the user, *anything* that
plausibly helps a med-school application should pass — it does not have to be
pharmacy or research.
"""
from __future__ import annotations

import re

# Domains that do not help a pre-med application. If any of these appear we
# exclude the job outright — these win even if a stray "lab"/"research" keyword
# also matched. Word-boundaried to avoid e.g. "engineer" matching "engineered".
_NONMED_TERMS: tuple[str, ...] = (
    # engineering / geoscience / construction
    "engineer", "engineering", "geotechnical", "geoscience", "civil",
    "structural", "mechanical", "electrical engineer", "materials testing",
    "concrete", "asphalt", "aggregate", "soil sampling", "construction",
    "surveyor", "architect", "drafting", "hvac", "electrician", "plumber",
    "welder", "welding", "machinist", "cnc", "fabrication", "millwright",
    # industrial / trades / logistics
    "automotive", "mechanic", "diesel", "warehouse", "forklift", "logistics",
    "supply chain", "manufacturing", "assembly line", "production operator",
    "mining", "petroleum", "oil and gas", "landscaping", "roofing",
    "inventory associate", "order picker", "order selector", "stock associate",
    # tech / office / finance — not clinical exposure
    "software", "web developer", "front-end", "back-end", "devops",
    "data engineer", "network administrator", "it support", "help desk",
    "accountant", "bookkeep", "payroll", "underwriter",
    "wealth management", "banking", "bank teller", "financial advisor",
    "investment", "actuary",
    # commission / non-clinical sales & admin-of-sales
    "sales representative", "sales associate", "sales consultant",
    "sales development", "business development", "account executive",
    "telemarket", "insurance agent", "real estate", "mortgage", "leasing",
    "recruiter", "seo specialist",
    # hazardous / environmental field services (industrial, not health)
    "hazardous waste", "waste management", "environmental field",
    "field service technician",
)

# Pre-med-positive signals. Any one makes the job relevant (assuming no
# non-medical domain term fired first).
_PREMED_TERMS: tuple[str, ...] = (
    # clinical / patient
    "clinical", "patient", "medical", "medicine", "health", "healthcare",
    "hospital", "clinic", "ward", "bedside", "vitals", "triage",
    "phlebotom", "scribe", "vaccin", "immuniz", "diagnostic imaging",
    "rehabilitation", "physiotherap", "occupational therap", "therapeutic",
    # pharmacy
    "pharmacy", "pharmaceutical", "pharma", "dispensing", "medication",
    # care work (great med-school narrative)
    "caregiver", "care aide", "personal support", "psw", "home care",
    "long-term care", "long term care", "hospice", "palliative",
    "developmental", "disability support", "autism", "behavioural",
    "behavioral", "mental health", "crisis", "counsel", "peer support",
    "elder", "senior care", "dementia", "respite",
    # life sciences / bench (biology context, not engineering)
    "biology", "biological", "biochem", "microbiology", "molecular",
    "genetic", "immunolog", "physiolog", "anatomy", "kinesiolog",
    "life science", "specimen", "cell culture", "histolog", "pathology",
    "clinical trial", "research assistant", "research study", "lab assistant",
    "epidemiolog", "public health", "community health", "nutrition",
    "dietary", "dental", "optometr", "audiolog", "veterinary", "animal care",
    # certs / service / leadership that read well pre-med
    "first aid", "cpr", "lifeguard", "camp counsel", "youth", "recreation",
    "volunteer", "child care", "childcare", "wellness",
)

_NONMED_RE = re.compile(
    r"(?<![a-z])(?:" + "|".join(re.escape(t) for t in _NONMED_TERMS) + r")",
    re.IGNORECASE,
)
_PREMED_RE = re.compile(
    r"(?<![a-z])(?:" + "|".join(re.escape(t) for t in _PREMED_TERMS) + r")",
    re.IGNORECASE,
)


def premed_relevance(title: str | None, description: str | None) -> tuple[bool, str]:
    """Return (True, '') if the job plausibly strengthens a med-school
    application, else (False, reason)."""
    text = f"{title or ''} {description or ''}"

    nonmed = _NONMED_RE.search(text)
    if nonmed:
        # An engineering/trades/sales role — unless it's genuinely clinical
        # (e.g. "Biomedical Engineer in a hospital"), drop it. Require a strong
        # clinical anchor in the *title* to override.
        title_clinical = _PREMED_RE.search(title or "")
        if not title_clinical:
            return False, f"non-medical domain ('{nonmed.group(0).lower()}')"

    if not _PREMED_RE.search(text):
        return False, "no pre-med relevance signal"

    return True, ""
