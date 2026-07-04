from __future__ import annotations

import pytest
from spacy.language import Language

from localjobscout.config import (
    BlendConfig,
    FocusConfig,
    SalaryConfig,
    ScrapersConfig,
    Settings,
)
from localjobscout.db import Job, make_job_id
from localjobscout.matcher import (
    SemanticMatcher,
    TfidfMatcher,
    _apply_blend,
    _compile_keywords,
    _target_fit,
    build_matcher,
)
from localjobscout.resume import preprocess


@pytest.fixture(scope="module")
def nlp() -> Language:
    try:
        import spacy

        model: Language = spacy.load("en_core_web_sm")
        return model
    except OSError:
        pytest.skip(
            "en_core_web_sm not installed — "
            "run: python -m spacy download en_core_web_sm"
        )


def _job(slug: str, description: str) -> Job:
    url = f"https://example.com/jobs/{slug}"
    return Job(
        id=make_job_id("test", url),
        source="test",
        title=slug.replace("-", " ").title(),
        url=url,
        description=description,
    )


# ---------------------------------------------------------------------------
# Fixtures — defined once at module level so the fixture just injects them
# ---------------------------------------------------------------------------

# Resume and STRONG are deliberately near-paraphrases of each other so the
# shared vocabulary overwhelms TF-IDF's IDF penalty in the small corpus.
RESUME = (
    "Senior Python engineer. Five years of professional experience. "
    "Expert in Django, PostgreSQL, and AWS. "
    "Built distributed data pipelines and REST APIs. "
    "Led backend engineering teams. "
    "Designed and deployed microservices architectures. "
    "Proficient in Python, SQL, Docker, and CI/CD pipelines."
)

STRONG = _job(
    "senior-python-backend",
    "Hiring a senior Python engineer with five years of professional experience. "
    "Expert Django, PostgreSQL, and AWS skills required. "
    "Will build distributed data pipelines and REST APIs. "
    "Will lead backend engineering teams. "
    "Designs and deploys microservices architectures. "
    "Proficient in Python, SQL, Docker, and CI/CD pipelines.",
)

WEAK = _job(
    "junior-frontend-react",
    "Junior frontend role. Must know React, CSS, and JavaScript. "
    "Basic understanding of REST APIs helpful but not required. No backend work.",
)

IRRELEVANT = _job(
    "registered-nurse-pediatric",
    "Registered nurse needed for pediatric ward. Experience with patient care, "
    "medication administration, and electronic medical records required. "
    "Valid nursing license mandatory.",
)

BIOLOGY_JOB = _job(
    "research-assistant-biology-lab",
    "Research assistant position in a university biology laboratory. "
    "Supports clinical research projects, specimen handling, and routine "
    "biology laboratory experiments. Prior laboratory or research experience "
    "preferred.",
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_strong_match_scores_highest(nlp: Language) -> None:
    matcher = TfidfMatcher(nlp)
    results = matcher.score_jobs(RESUME, [STRONG, WEAK, IRRELEVANT])
    scores = {job.id: score for job, score in results}
    assert scores[STRONG.id] > scores[WEAK.id]
    assert scores[STRONG.id] > scores[IRRELEVANT.id]
    assert scores[STRONG.id] > 0.3, (
        f"Expected strong match > 0.3, got {scores[STRONG.id]:.3f}"
    )
    assert scores[IRRELEVANT.id] < 0.1, (
        f"Expected irrelevant < 0.1, got {scores[IRRELEVANT.id]:.3f}"
    )


def test_results_sorted_descending(nlp: Language) -> None:
    matcher = TfidfMatcher(nlp)
    # Deliberately pass in reverse order to confirm sort is applied
    results = matcher.score_jobs(RESUME, [IRRELEVANT, STRONG, WEAK])
    raw_scores = [s for _, s in results]
    assert raw_scores == sorted(raw_scores, reverse=True)


def test_empty_jobs_returns_empty_list(nlp: Language) -> None:
    matcher = TfidfMatcher(nlp)
    assert matcher.score_jobs(RESUME, []) == []


def test_empty_description_gets_zero_and_does_not_crash(nlp: Language) -> None:
    empty_job = _job("no-description", "")
    matcher = TfidfMatcher(nlp)
    results = matcher.score_jobs(RESUME, [empty_job, STRONG])
    scores = {job.id: score for job, score in results}
    assert scores[empty_job.id] == 0.0
    assert scores[STRONG.id] > 0.0


def test_deterministic_scores(nlp: Language) -> None:
    matcher = TfidfMatcher(nlp)
    jobs = [STRONG, WEAK, IRRELEVANT]
    run1 = [(j.id, s) for j, s in matcher.score_jobs(RESUME, jobs)]
    run2 = [(j.id, s) for j, s in matcher.score_jobs(RESUME, jobs)]
    assert run1 == run2


def test_preprocess_collapses_inflections(nlp: Language) -> None:
    # "engineers" and "engineered" should share the same lemma ("engineer");
    # at minimum two of the three surface forms collapse to one token.
    result = preprocess("engineering engineers engineered", nlp)
    # Exclude underscore-joined noun-chunk tokens to count base lemmas only.
    base_tokens = [t for t in result.split() if "_" not in t]
    counts: dict[str, int] = {}
    for t in base_tokens:
        counts[t] = counts.get(t, 0) + 1
    assert max(counts.values()) >= 2, (
        f"Expected at least two tokens to share a lemma, got: {base_tokens}"
    )


# ---------------------------------------------------------------------------
# Focus boost
# ---------------------------------------------------------------------------


def test_focus_boost_increases_score(nlp: Language) -> None:
    """Same job, focus keyword present → boosted score > no-boost score."""
    job = _job("python-clinical", "Python developer for clinical research systems.")
    no_boost = TfidfMatcher(nlp, focus=FocusConfig())
    with_boost = TfidfMatcher(
        nlp, focus=FocusConfig(keywords=["clinical"], boost_per_hit=0.1, max_boost=0.5)
    )
    base_score = no_boost.score_jobs(RESUME, [job])[0][1]
    boosted_score = with_boost.score_jobs(RESUME, [job])[0][1]
    assert boosted_score > base_score


def test_focus_boost_ignores_substring_matches(nlp: Language) -> None:
    """'lab' must not match inside 'collaborate' — word-boundary only."""
    job = _job(
        "collab-role", "Collaborate with the database team on integrations."
    )
    no_boost = TfidfMatcher(nlp, focus=FocusConfig())
    with_boost = TfidfMatcher(
        nlp,
        focus=FocusConfig(
            keywords=["lab", "data"], boost_per_hit=0.1, max_boost=0.5
        ),
    )
    base_score = no_boost.score_jobs(RESUME, [job])[0][1]
    boosted_score = with_boost.score_jobs(RESUME, [job])[0][1]
    assert boosted_score == pytest.approx(base_score)


def test_focus_boost_capped_at_max_boost(nlp: Language) -> None:
    """Many keywords hit → total boost limited to max_boost."""
    focus = FocusConfig(
        keywords=["python", "django", "sql", "docker", "aws"],
        boost_per_hit=0.1,
        max_boost=0.2,
    )
    matcher = TfidfMatcher(nlp, focus=focus)
    base_matcher = TfidfMatcher(nlp)
    job = _job("all-keywords", "Python Django SQL Docker AWS microservices.")

    base = base_matcher.score_jobs(RESUME, [job])[0][1]
    boosted = matcher.score_jobs(RESUME, [job])[0][1]
    # All 5 keywords → potential 0.5, capped at 0.2
    assert boosted <= base + 0.2 + 1e-9


def test_focus_empty_keywords_no_boost(nlp: Language) -> None:
    """Empty keyword list → identical score to default matcher."""
    no_focus = TfidfMatcher(nlp, focus=FocusConfig(keywords=[]))
    default = TfidfMatcher(nlp)
    assert no_focus.score_jobs(RESUME, [STRONG])[0][1] == \
        default.score_jobs(RESUME, [STRONG])[0][1]


def test_focus_score_capped_at_1_0(nlp: Language) -> None:
    """Base + boost never exceeds 1.0."""
    focus = FocusConfig(keywords=["python", "django"], boost_per_hit=0.5, max_boost=1.0)
    matcher = TfidfMatcher(nlp, focus=focus)
    results = matcher.score_jobs(RESUME, [STRONG])
    assert results[0][1] <= 1.0


def test_focus_keyword_counted_once_per_keyword(nlp: Language) -> None:
    """Keyword appearing multiple times in text still counts as 1 hit."""
    focus = FocusConfig(keywords=["clinical"], boost_per_hit=0.1, max_boost=0.5)
    matcher = TfidfMatcher(nlp, focus=focus)
    # One keyword → max boost contribution = 0.1
    job = _job("repeated-keyword", "clinical clinical clinical clinical roles.")
    base_matcher = TfidfMatcher(nlp)
    base = base_matcher.score_jobs(RESUME, [job])[0][1]
    boosted = matcher.score_jobs(RESUME, [job])[0][1]
    # Boost should be exactly boost_per_hit (0.1), not 4×
    assert boosted <= base + 0.1 + 1e-9


# ---------------------------------------------------------------------------
# Resume vocabulary expansion
# ---------------------------------------------------------------------------


def test_expand_resume_increases_biology_job_score(nlp: Language) -> None:
    """Expansion injects focus keywords into the resume so TF-IDF finds overlap."""
    keywords = ["biology", "laboratory", "research", "clinical"]
    unexpanded = TfidfMatcher(
        nlp,
        focus=FocusConfig(keywords=keywords, expand_resume=False),
    )
    expanded = TfidfMatcher(
        nlp,
        focus=FocusConfig(
            keywords=keywords, expand_resume=True, expand_weight=3
        ),
    )
    base_score = unexpanded.score_jobs(RESUME, [BIOLOGY_JOB])[0][1]
    expanded_score = expanded.score_jobs(RESUME, [BIOLOGY_JOB])[0][1]
    assert expanded_score > base_score


def test_expand_resume_disabled_by_default(nlp: Language) -> None:
    """Default FocusConfig() leaves scoring identical to a bare TfidfMatcher."""
    default_focus = TfidfMatcher(nlp, focus=FocusConfig())
    default = TfidfMatcher(nlp)
    assert default_focus.score_jobs(RESUME, [BIOLOGY_JOB])[0][1] == \
        default.score_jobs(RESUME, [BIOLOGY_JOB])[0][1]


def test_expand_weight_zero_is_no_op(nlp: Language) -> None:
    """expand_weight=0 produces the same score as expand_resume=False."""
    keywords = ["biology", "laboratory", "research", "clinical"]
    off = TfidfMatcher(
        nlp,
        focus=FocusConfig(keywords=keywords, expand_resume=False),
    )
    zero_weight = TfidfMatcher(
        nlp,
        focus=FocusConfig(
            keywords=keywords, expand_resume=True, expand_weight=0
        ),
    )
    off_score = off.score_jobs(RESUME, [BIOLOGY_JOB])[0][1]
    zero_score = zero_weight.score_jobs(RESUME, [BIOLOGY_JOB])[0][1]
    assert zero_score == off_score


def test_expand_resume_does_not_inflate_unrelated_job(nlp: Language) -> None:
    """Biology expansion should not lift a plumbing job into match range."""
    plumbing_job = _job(
        "residential-plumber",
        "Licensed residential plumber needed. Install and repair pipes, "
        "drains, water heaters, and fixtures. Experience with copper soldering, "
        "PEX, and HVAC rough-in required. Valid driver's license mandatory.",
    )
    expanded = TfidfMatcher(
        nlp,
        focus=FocusConfig(
            keywords=["biology", "laboratory", "research", "clinical"],
            expand_resume=True,
            expand_weight=3,
        ),
    )
    score = expanded.score_jobs(RESUME, [plumbing_job])[0][1]
    assert score < 0.2, f"Expected plumbing job < 0.2, got {score:.3f}"


# ---------------------------------------------------------------------------
# Title-priority boost
# ---------------------------------------------------------------------------


def test_title_match_scores_higher_than_description_match(nlp: Language) -> None:
    """Keyword in title beats keyword in description with multiplier > 1."""
    job_kw_in_title = _job(
        "clinical-research-role",
        "Support role in a scientific environment. General administrative duties.",
    )
    job_kw_in_desc = _job(
        "admin-support-role",
        "Administrative support position. "
        "Experience in clinical research environments preferred.",
    )
    matcher = TfidfMatcher(
        nlp,
        focus=FocusConfig(
            keywords=["clinical"],
            boost_per_hit=0.1,
            max_boost=0.5,
            title_boost_multiplier=3.0,
            expand_resume=False,
        ),
    )
    results = matcher.score_jobs(RESUME, [job_kw_in_title, job_kw_in_desc])
    scores = {job.id: score for job, score in results}
    assert scores[job_kw_in_title.id] > scores[job_kw_in_desc.id]


def test_title_boost_multiplier_one_equals_description_boost(nlp: Language) -> None:
    """title_boost_multiplier=1.0 makes title and description hits equal."""
    JOB_KW_IN_TITLE = _job(
        "clinical-research-role",
        "Support role in a scientific environment. General administrative duties.",
    )
    JOB_KW_IN_DESC = _job(
        "admin-support-role",
        "Administrative support position. "
        "Experience in clinical research environments preferred.",
    )
    focus = FocusConfig(
        keywords=["clinical"],
        boost_per_hit=0.1,
        max_boost=0.5,
        title_boost_multiplier=1.0,
        expand_resume=False,
    )
    matcher_with_boost = TfidfMatcher(nlp, focus=focus)
    matcher_no_boost = TfidfMatcher(nlp, focus=FocusConfig(keywords=[]))

    title_boosted = matcher_with_boost.score_jobs(RESUME, [JOB_KW_IN_TITLE])[0][1]
    title_base = matcher_no_boost.score_jobs(RESUME, [JOB_KW_IN_TITLE])[0][1]
    desc_boosted = matcher_with_boost.score_jobs(RESUME, [JOB_KW_IN_DESC])[0][1]
    desc_base = matcher_no_boost.score_jobs(RESUME, [JOB_KW_IN_DESC])[0][1]

    title_boost_delta = title_boosted - title_base
    desc_boost_delta = desc_boosted - desc_base

    assert title_boost_delta == pytest.approx(desc_boost_delta)


def test_title_boost_still_capped_by_max_boost(nlp: Language) -> None:
    """An aggressive title multiplier cannot exceed max_boost."""
    job = _job(
        "medical-clinical-hospital-patient",
        "Some unrelated text.",
    )
    matcher = TfidfMatcher(
        nlp,
        focus=FocusConfig(
            keywords=["medical", "clinical", "hospital", "patient"],
            boost_per_hit=0.1,
            max_boost=0.2,
            title_boost_multiplier=5.0,
            expand_resume=False,
        ),
    )
    base_matcher = TfidfMatcher(nlp)
    base = base_matcher.score_jobs(RESUME, [job])[0][1]
    boosted = matcher.score_jobs(RESUME, [job])[0][1]
    assert boosted <= base + 0.2 + 1e-9


# ---------------------------------------------------------------------------
# build_matcher selection + salary boost integration + semantic degradation
# ---------------------------------------------------------------------------


def _settings(**kwargs: object) -> Settings:
    base: dict[str, object] = dict(scrapers=ScrapersConfig())
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def test_build_matcher_returns_tfidf_by_default(nlp: Language) -> None:
    matcher = build_matcher(_settings(use_semantic_matcher=False), nlp)
    assert isinstance(matcher, TfidfMatcher)


def test_build_matcher_selects_semantic_when_configured(nlp: Language) -> None:
    """With use_semantic_matcher=True and sentence-transformers absent, building
    raises a clear RuntimeError naming the optional extra."""
    settings = _settings(use_semantic_matcher=True)
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        with pytest.raises(RuntimeError, match="sentence-transformers"):
            build_matcher(settings, nlp)
    else:
        matcher = build_matcher(settings, nlp)
        assert isinstance(matcher, SemanticMatcher)


def test_semantic_matcher_raises_without_dependency(nlp: Language) -> None:
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        with pytest.raises(RuntimeError, match="pip install"):
            SemanticMatcher(nlp)
    else:
        pytest.skip("sentence-transformers installed; degradation path not exercised")


def test_salary_boost_applied_in_tfidf(nlp: Language) -> None:
    """A job advertising high pay outscores an identical job with no salary."""
    paid = _job("lab-tech-paid", "Laboratory technician role.")
    paid.salary_max = 60_000
    unpaid = _job("lab-tech-unpaid", "Laboratory technician role.")

    salary = SalaryConfig(enabled=True, target_annual=40_000, boost=0.2)
    matcher = TfidfMatcher(nlp, salary=salary)
    scored = dict(
        (j.id, s) for j, s in matcher.score_jobs(RESUME, [paid, unpaid])
    )
    assert scored[paid.id] > scored[unpaid.id]


def test_salary_boost_disabled_leaves_scores_equal(nlp: Language) -> None:
    paid = _job("lab-tech-paid2", "Laboratory technician role.")
    paid.salary_max = 60_000
    unpaid = _job("lab-tech-unpaid2", "Laboratory technician role.")

    matcher = TfidfMatcher(nlp)  # salary disabled by default
    scored = dict(
        (j.id, s) for j, s in matcher.score_jobs(RESUME, [paid, unpaid])
    )
    assert scored[paid.id] == scored[unpaid.id]


# ---------------------------------------------------------------------------
# Scoring blend (Step 2): combined = w_resume*resume_fit + w_target*target_fit
# ---------------------------------------------------------------------------


def test_target_fit_title_hit_weighted_and_saturated() -> None:
    blend = BlendConfig(
        enabled=True,
        target_keywords=["python", "django"],
        title_boost_multiplier=2.0,
        target_saturation=6.0,
    )
    # "python" in title → 2.0; "django" in desc → 1.0; total 3.0 / 6.0 = 0.5
    job = _job("python-dev", "Build services with Django and PostgreSQL.")
    patterns = _compile_keywords(blend.target_keywords)
    assert _target_fit(job, blend, patterns) == pytest.approx(0.5)


def test_target_fit_zero_without_keywords() -> None:
    blend = BlendConfig(enabled=True, target_keywords=[])
    patterns = _compile_keywords(blend.target_keywords)
    assert _target_fit(_job("x", "anything"), blend, patterns) == 0.0


def test_apply_blend_combines_weights() -> None:
    blend = BlendConfig(
        enabled=True,
        w_resume=0.6,
        w_target=0.4,
        target_keywords=["python"],
        title_boost_multiplier=2.0,
        target_saturation=2.0,  # one title hit → target_fit = 1.0
    )
    job = _job("python-role", "no keyword here")  # title has 'python'
    # resume_fit = 0.5; target_fit = 1.0 → 0.6*0.5 + 0.4*1.0 = 0.7
    out = _apply_blend([(job, 0.5)], blend)
    assert out[0][1] == pytest.approx(0.7)


def test_blend_active_skips_focus_boost(nlp: Language) -> None:
    """With blend enabled, the legacy focus boost is NOT applied — relevance
    comes from target_fit instead."""
    job = _job("python-clinical", "Python developer for clinical research.")
    focus = FocusConfig(keywords=["clinical"], boost_per_hit=0.5, max_boost=0.5)
    # blend with no target keywords → target_fit 0 → combined = w_resume*cosine
    blend = BlendConfig(enabled=True, w_resume=1.0, w_target=0.0, target_keywords=[])
    blended = TfidfMatcher(nlp, focus=focus, blend=blend)
    plain = TfidfMatcher(nlp, focus=FocusConfig(keywords=[]))
    # focus boost would have lifted the score; blend ignores it
    assert blended.score_jobs(RESUME, [job])[0][1] == pytest.approx(
        plain.score_jobs(RESUME, [job])[0][1]
    )


def test_build_matcher_wires_active_profile_blend(nlp: Language) -> None:
    """build_matcher attaches a blend from the active profile (broad default)."""
    settings = _settings()
    m = build_matcher(settings, nlp)
    assert isinstance(m, TfidfMatcher)
    assert m._blend is not None and m._blend.enabled
    # broad profile = all enabled category keywords as target keywords
    assert "software" in m._blend.target_keywords
    assert m._blend.w_resume == pytest.approx(0.6)


def test_biomed_profile_narrows_target_keywords(nlp: Language) -> None:
    settings = _settings(active_profile="biomed")
    m = build_matcher(settings, nlp)
    assert isinstance(m, TfidfMatcher)
    assert m._blend is not None
    # biomed targets lab_research only → no dev keywords
    assert "research" in m._blend.target_keywords
    assert "software" not in m._blend.target_keywords
    assert m._blend.w_target == pytest.approx(0.6)
