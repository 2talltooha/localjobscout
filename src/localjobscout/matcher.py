from __future__ import annotations

from typing import Any, Protocol

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from spacy.language import Language

from localjobscout.config import FocusConfig, GeoConfig, SalaryConfig, Settings
from localjobscout.db import Job
from localjobscout.resume import preprocess


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
    keywords_lower = [kw.lower() for kw in focus.keywords]
    boosted: list[tuple[Job, float]] = []
    for job, base_score in paired:
        title_lower = job.title.lower()
        desc_lower = job.description.lower()
        hits: float = 0.0
        for kw in keywords_lower:
            if kw in title_lower:
                hits += focus.title_boost_multiplier
            elif kw in desc_lower:
                hits += 1.0
        boost = min(hits * focus.boost_per_hit, focus.max_boost)
        boosted.append((job, min(base_score + boost, 1.0)))
    return boosted


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
        focus: FocusConfig = FocusConfig(),
        salary: SalaryConfig = SalaryConfig(),
        geo: GeoConfig = GeoConfig(),
    ) -> None:
        self._nlp = nlp
        self._focus = focus
        self._salary = salary
        self._geo = geo

    def score_jobs(self, resume_text: str, jobs: list[Job]) -> list[tuple[Job, float]]:
        if not jobs:
            return []

        scoring_resume = resume_text
        if self._focus.expand_resume and self._focus.keywords:
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

    def __init__(
        self,
        nlp: Language,
        focus: FocusConfig = FocusConfig(),
        salary: SalaryConfig = SalaryConfig(),
        geo: GeoConfig = GeoConfig(),
    ) -> None:
        self._nlp = nlp
        self._focus = focus
        self._salary = salary
        self._geo = geo
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

        resume_emb = self._model.encode(resume_text[:1024], convert_to_tensor=True)
        job_texts = [f"{j.title}. {j.description}"[:512] for j in jobs]
        job_embs = self._model.encode(job_texts, convert_to_tensor=True)
        raw_sims: list[float] = util.cos_sim(resume_emb, job_embs).squeeze(0).tolist()

        paired: list[tuple[Job, float]] = list(
            zip(jobs, [max(0.0, float(s)) for s in raw_sims], strict=True)
        )
        paired = _apply_focus_boost(paired, self._focus)
        paired = _apply_salary_boost(paired, self._salary)
        paired = _apply_location_boost(paired, self._geo)
        paired.sort(key=lambda x: x[1], reverse=True)
        return paired


def build_matcher(settings: Settings, nlp: Language) -> Matcher:
    if settings.use_semantic_matcher:
        return SemanticMatcher(
            nlp, focus=settings.focus, salary=settings.salary, geo=settings.geo
        )
    return TfidfMatcher(
        nlp, focus=settings.focus, salary=settings.salary, geo=settings.geo
    )
