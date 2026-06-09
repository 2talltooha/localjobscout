"""Resume A/B comparison.

Score the job corpus against multiple resume variants and report which resume
performs best — overall (average score, number of wins) and per job. Helps the
user decide which resume to keep as the primary, or which to use for a specific
application.

The core ``compare_resumes`` function takes any object with a
``score_jobs(resume_text, jobs) -> list[(Job, float)]`` method, so it is
testable with a lightweight fake matcher (no spaCy/torch needed).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from localjobscout.db import Job


class _Scorer(Protocol):
    def score_jobs(
        self, resume_text: str, jobs: list[Job]
    ) -> list[tuple[Job, float]]: ...


@dataclass
class ResumeVariant:
    label: str
    text: str


@dataclass
class JobBest:
    job: Job
    best_label: str
    best_score: float
    scores: dict[str, float]


@dataclass
class ComparisonSummary:
    wins: dict[str, int] = field(default_factory=dict)
    avg_score: dict[str, float] = field(default_factory=dict)
    above_threshold: dict[str, int] = field(default_factory=dict)

    @property
    def overall_winner(self) -> str | None:
        """Label with the highest average score, or None when there's no signal."""
        if not self.avg_score:
            return None
        best = max(self.avg_score, key=lambda k: self.avg_score[k])
        # No meaningful winner when every resume scored zero (e.g. no results).
        if self.avg_score[best] <= 0.0:
            return None
        return best


def load_variants(
    primary: Path,
    extra: list[Path],
    *,
    loader: object = None,
) -> list[ResumeVariant]:
    """Load primary + extra resume files into labelled variants.

    Labels are the file stems. Missing files are skipped. ``loader`` is an
    optional callable(Path) -> str (defaults to localjobscout.resume.load_resume).
    Importing the default loader lazily keeps this module spaCy-free for tests.
    """
    if loader is None:
        from localjobscout.resume import load_resume as _load
        load = _load
    else:
        load = loader  # type: ignore[assignment]

    variants: list[ResumeVariant] = []
    seen_labels: set[str] = set()
    for path in [primary, *extra]:
        try:
            text = load(path)
        except FileNotFoundError:
            continue
        label = path.stem
        # Disambiguate duplicate stems
        if label in seen_labels:
            label = f"{label} ({path.parent.name})"
        seen_labels.add(label)
        variants.append(ResumeVariant(label=label, text=text))
    return variants


def compare_resumes(
    matcher: _Scorer,
    variants: list[ResumeVariant],
    jobs: list[Job],
) -> list[JobBest]:
    """Score every job against every variant; return per-job best match.

    Result is sorted by best_score descending.
    """
    if not variants or not jobs:
        return []

    per_variant: dict[str, dict[str, float]] = {}
    for v in variants:
        scored = matcher.score_jobs(v.text, jobs)
        per_variant[v.label] = {job.id: score for job, score in scored}

    results: list[JobBest] = []
    for job in jobs:
        scores = {
            label: per_variant[label].get(job.id, 0.0)
            for label in per_variant
        }
        best_label = max(scores, key=lambda k: scores[k])
        results.append(
            JobBest(
                job=job,
                best_label=best_label,
                best_score=scores[best_label],
                scores=scores,
            )
        )
    results.sort(key=lambda r: r.best_score, reverse=True)
    return results


def summarize(
    results: list[JobBest],
    labels: list[str],
    *,
    threshold: float = 0.0,
) -> ComparisonSummary:
    """Aggregate per-job results into wins / average / above-threshold counts."""
    summary = ComparisonSummary(
        wins={label: 0 for label in labels},
        avg_score={label: 0.0 for label in labels},
        above_threshold={label: 0 for label in labels},
    )
    if not results:
        return summary

    totals: dict[str, float] = {label: 0.0 for label in labels}
    for r in results:
        summary.wins[r.best_label] = summary.wins.get(r.best_label, 0) + 1
        for label in labels:
            s = r.scores.get(label, 0.0)
            totals[label] += s
            if s >= threshold:
                summary.above_threshold[label] += 1

    n = len(results)
    summary.avg_score = {label: totals[label] / n for label in labels}
    return summary
