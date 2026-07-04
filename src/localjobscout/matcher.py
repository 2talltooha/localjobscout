from __future__ import annotations

import re
from typing import Any, Protocol

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from spacy.language import Language

from localjobscout.config import (
    BlendConfig,
    FocusConfig,
    GeoConfig,
    SalaryConfig,
    Settings,
)
from localjobscout.db import Job
from localjobscout.resume import preprocess


def _keyword_hit(keyword_pattern: re.Pattern[str], text: str) -> bool:
    """Whole-word/phrase match — NOT substring. Mirrors config._keyword_matches
    so scoring and classification never disagree (e.g. 'lab' must not match
    inside 'collaborate', 'data' must not match inside 'database')."""
    return keyword_pattern.search(text) is not None


def _compile_keywords(keywords: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(rf"\b{re.escape(kw.lower())}\b") for kw in keywords]


class Matcher(Protocol):
    def score_jobs(
        self, resume_text: str, jobs: list[Job]
    ) -> list[tuple[Job, float]]: ...


def _apply_salary_boost(
    paired: list[tuple[Job, float]], salary: SalaryConfig
) -> list[tuple[Job, float]]:
    """Boost jobs advertising pay at/above target_annual.

    Boost scales linearly from 0 (no salary / very low) to `salary.boost`
    (at or above target_annual), using the job's annualized salary_max when
    present, else salary_min. Jobs with no salary are returned unchanged.
    """
    if not salary.enabled or salary.target_annual <= 0:
        return paired
    boosted: list[tuple[Job, float]] = []
    for job, score in paired:
        pay = job.salary_max if job.salary_max is not None else job.salary_min
        if pay is None or pay <= 0:
            boosted.append((job, score))
            continue
        ratio = min(pay / salary.target_annual, 1.0)
        boosted.append((job, min(score + salary.boost * ratio, 1.0)))
    return boosted


def _apply_focus_boost(
    paired: list[tuple[Job, float]], focus: FocusConfig
) -> list[tuple[Job, float]]:
    if not focus.keywords:
        return paired
    patterns = _compile_keywords(focus.keywords)
    boosted: list[tuple[Job, float]] = []
    for job, base_score in paired:
        title_lower = job.title.lower()
        desc_lower = job.description.lower()
        hits: float = 0.0
        for pat in patterns:
            if _keyword_hit(pat, title_lower):
                hits += focus.title_boost_multiplier
            elif _keyword_hit(pat, desc_lower):
                hits += 1.0
        boost = min(hits * focus.boost_per_hit, focus.max_boost)
        boosted.append((job, min(base_score + boost, 1.0)))
    return boosted


def _target_fit(
    job: Job, blend: BlendConfig, patterns: list[re.Pattern[str]]
) -> float:
    """0–1 keyword-hit relevance of a job to the active profile's target
    keywords. Title hits weighted by ``title_boost_multiplier``; total hits
    saturated at ``target_saturation``."""
    if not patterns or blend.target_saturation <= 0:
        return 0.0
    title_lower = job.title.lower()
    desc_lower = job.description.lower()
    hits = 0.0
    for pat in patterns:
        if _keyword_hit(pat, title_lower):
            hits += blend.title_boost_multiplier
        elif _keyword_hit(pat, desc_lower):
            hits += 1.0
    return min(hits / blend.target_saturation, 1.0)


def _apply_blend(
    paired: list[tuple[Job, float]], blend: BlendConfig
) -> list[tuple[Job, float]]:
    """Combine raw resume_fit with target_fit:
    ``combined = w_resume*resume_fit + w_target*target_fit``. Replaces the
    legacy additive focus boost. Salary/geo boosts are applied afterwards."""
    patterns = _compile_keywords(blend.target_keywords)
    blended: list[tuple[Job, float]] = []
    for job, resume_fit in paired:
        tgt = _target_fit(job, blend, patterns)
        combined = blend.w_resume * resume_fit + blend.w_target * tgt
        blended.append((job, min(combined, 1.0)))
    return blended


def _apply_location_boost(
    paired: list[tuple[Job, float]], geo: GeoConfig
) -> list[tuple[Job, float]]:
    """Add `geo.boost` to jobs located in one of the home cities. Soft boost —
    never excludes anywhere, just lifts hometown roles up the ranking."""
    if not geo.home_cities or geo.boost <= 0:
        return paired
    cities = [c.lower() for c in geo.home_cities]
    boosted: list[tuple[Job, float]] = []
    for job, score in paired:
        loc = (job.location or "").lower()
        if any(city in loc for city in cities):
            boosted.append((job, min(score + geo.boost, 1.0)))
        else:
            boosted.append((job, score))
    return boosted


class TfidfMatcher:
    def __init__(
        self,
        nlp: Language,
        focus: FocusConfig | None = None,
        salary: SalaryConfig | None = None,
        geo: GeoConfig | None = None,
        blend: BlendConfig | None = None,
    ) -> None:
        self._nlp = nlp
        self._focus = focus if focus is not None else FocusConfig()
        self._salary = salary if salary is not None else SalaryConfig()
        self._geo = geo if geo is not None else GeoConfig()
        self._blend = blend

    def score_jobs(self, resume_text: str, jobs: list[Job]) -> list[tuple[Job, float]]:
        if not jobs:
            return []

        blend_active = self._blend is not None and self._blend.enabled
        # Resume expansion is a focus-boost feature; with the blend active the
        # target-fit term carries relevance, so resume_fit stays pure cosine.
        scoring_resume = resume_text
        if not blend_active and self._focus.expand_resume and self._focus.keywords:
            expansion = " ".join(self._focus.keywords * self._focus.expand_weight)
            scoring_resume = resume_text + " " + expansion
        resume_pp = preprocess(scoring_resume, self._nlp)
        job_texts = [preprocess(job.description, self._nlp) for job in jobs]

        # Jobs with empty preprocessed descriptions get score 0.0 and are
        # excluded from vectorizer fit to avoid all-zero-row edge cases.
        non_empty = [i for i, t in enumerate(job_texts) if t.strip()]
        scores = [0.0] * len(jobs)

        if non_empty and resume_pp.strip():
            corpus = [resume_pp] + [job_texts[i] for i in non_empty]
            vectorizer = TfidfVectorizer(
                ngram_range=(1, 2), min_df=1, sublinear_tf=True
            )
            matrix = vectorizer.fit_transform(corpus)
            sims = cosine_similarity(matrix[0], matrix[1:]).flatten()
            for rank, orig_idx in enumerate(non_empty):
                scores[orig_idx] = float(sims[rank])

        paired = list(zip(jobs, scores, strict=True))
        if blend_active:
            assert self._blend is not None
            paired = _apply_blend(paired, self._blend)
        else:
            paired = _apply_focus_boost(paired, self._focus)
        paired = _apply_salary_boost(paired, self._salary)
        paired = _apply_location_boost(paired, self._geo)
        paired.sort(key=lambda x: x[1], reverse=True)
        return paired


class SemanticMatcher:
    """sentence-transformers cosine-similarity matcher.

    Better than TF-IDF at paraphrase matching ("lab assistant" vs "research
    technician"). Requires: pip install localjobscout[semantic]
    """

    # all-MiniLM-L6-v2's max sequence length is 256 word-piece tokens —
    # roughly 1200-1500 characters of typical English. sentence-transformers
    # truncates internally at that point regardless, so these char caps exist
    # only to bound encode() cost on text that's already far longer (some
    # scraped descriptions run 8000+ chars). Previously capped at 1024/512,
    # which threw away content well short of what the model could actually
    # use — raised to track the model's real budget instead.
    _RESUME_CHAR_CAP = 1500
    _JOB_TEXT_CHAR_CAP = 1500

    def __init__(
        self,
        nlp: Language,
        focus: FocusConfig | None = None,
        salary: SalaryConfig | None = None,
        geo: GeoConfig | None = None,
        blend: BlendConfig | None = None,
    ) -> None:
        self._nlp = nlp
        self._focus = focus if focus is not None else FocusConfig()
        self._salary = salary if salary is not None else SalaryConfig()
        self._geo = geo if geo is not None else GeoConfig()
        self._blend = blend
        try:
            from sentence_transformers import (
                SentenceTransformer,
            )
            self._model: Any = SentenceTransformer("all-MiniLM-L6-v2")
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers not installed. "
                "Run: pip install localjobscout[semantic]"
            ) from exc

    def score_jobs(self, resume_text: str, jobs: list[Job]) -> list[tuple[Job, float]]:
        if not jobs:
            return []
        from sentence_transformers import util

        resume_emb = self._model.encode(
            resume_text[: self._RESUME_CHAR_CAP], convert_to_tensor=True
        )
        job_texts = [
            f"{j.title}. {j.description}"[: self._JOB_TEXT_CHAR_CAP] for j in jobs
        ]
        job_embs = self._model.encode(job_texts, convert_to_tensor=True)
        raw_sims: list[float] = util.cos_sim(resume_emb, job_embs).squeeze(0).tolist()

        paired: list[tuple[Job, float]] = list(
            zip(jobs, [max(0.0, float(s)) for s in raw_sims], strict=True)
        )
        if self._blend is not None and self._blend.enabled:
            paired = _apply_blend(paired, self._blend)
        else:
            paired = _apply_focus_boost(paired, self._focus)
        paired = _apply_salary_boost(paired, self._salary)
        paired = _apply_location_boost(paired, self._geo)
        paired.sort(key=lambda x: x[1], reverse=True)
        return paired


def build_matcher(settings: Settings, nlp: Language) -> Matcher:
    blend = settings.build_blend()
    if settings.use_semantic_matcher:
        return SemanticMatcher(
            nlp, focus=settings.focus, salary=settings.salary,
            geo=settings.geo, blend=blend,
        )
    return TfidfMatcher(
        nlp, focus=settings.focus, salary=settings.salary,
        geo=settings.geo, blend=blend,
    )
