from __future__ import annotations

from pathlib import Path

from localjobscout.db import Job, make_job_id
from localjobscout.resume_ab import (
    ResumeVariant,
    compare_resumes,
    load_variants,
    summarize,
)


def _job(title: str) -> Job:
    url = f"https://x.com/{title}"
    return Job(
        id=make_job_id("t", url),
        source="t",
        title=title,
        url=url,
        description="",
    )


class _FakeMatcher:
    """Returns predetermined scores per (resume_text, job.id)."""

    def __init__(self, table: dict[str, dict[str, float]]) -> None:
        # table[resume_text][job_id] = score
        self._table = table

    def score_jobs(
        self, resume_text: str, jobs: list[Job]
    ) -> list[tuple[Job, float]]:
        scores = self._table[resume_text]
        return [(j, scores.get(j.id, 0.0)) for j in jobs]


def test_compare_picks_best_per_job() -> None:
    j1, j2 = _job("A"), _job("B")
    table = {
        "resumeA": {j1.id: 0.8, j2.id: 0.2},
        "resumeB": {j1.id: 0.3, j2.id: 0.9},
    }
    matcher = _FakeMatcher(table)
    variants = [
        ResumeVariant("A", "resumeA"),
        ResumeVariant("B", "resumeB"),
    ]
    results = compare_resumes(matcher, variants, [j1, j2])

    by_job = {r.job.id: r for r in results}
    assert by_job[j1.id].best_label == "A"
    assert by_job[j1.id].best_score == 0.8
    assert by_job[j2.id].best_label == "B"
    assert by_job[j2.id].best_score == 0.9


def test_compare_sorts_by_best_score_desc() -> None:
    j1, j2 = _job("A"), _job("B")
    table = {
        "r1": {j1.id: 0.3, j2.id: 0.9},
        "r2": {j1.id: 0.1, j2.id: 0.2},
    }
    matcher = _FakeMatcher(table)
    variants = [ResumeVariant("r1", "r1"), ResumeVariant("r2", "r2")]
    results = compare_resumes(matcher, variants, [j1, j2])
    assert results[0].job.id == j2.id  # 0.9 first


def test_compare_empty_inputs() -> None:
    matcher = _FakeMatcher({})
    assert compare_resumes(matcher, [], [_job("A")]) == []
    assert compare_resumes(matcher, [ResumeVariant("A", "x")], []) == []


def test_summarize_counts_wins_and_avg() -> None:
    j1, j2 = _job("A"), _job("B")
    table = {
        "rA": {j1.id: 0.8, j2.id: 0.2},
        "rB": {j1.id: 0.3, j2.id: 0.9},
    }
    matcher = _FakeMatcher(table)
    variants = [ResumeVariant("A", "rA"), ResumeVariant("B", "rB")]
    results = compare_resumes(matcher, variants, [j1, j2])
    summary = summarize(results, ["A", "B"], threshold=0.5)

    assert summary.wins == {"A": 1, "B": 1}
    # avg A = (0.8+0.2)/2 = 0.5 ; avg B = (0.3+0.9)/2 = 0.6
    assert abs(summary.avg_score["A"] - 0.5) < 1e-9
    assert abs(summary.avg_score["B"] - 0.6) < 1e-9
    assert summary.overall_winner == "B"
    # above threshold 0.5: A has j1(0.8) → 1 ; B has j2(0.9) → 1
    assert summary.above_threshold == {"A": 1, "B": 1}


def test_summarize_empty() -> None:
    summary = summarize([], ["A", "B"])
    assert summary.wins == {"A": 0, "B": 0}
    assert summary.overall_winner is None


def test_load_variants_uses_stem_labels(tmp_path: Path) -> None:
    primary = tmp_path / "resume.txt"
    primary.write_text("primary text")
    alt = tmp_path / "resume-clinical.txt"
    alt.write_text("clinical text")

    variants = load_variants(
        primary, [alt], loader=lambda p: Path(p).read_text()
    )
    assert [v.label for v in variants] == ["resume", "resume-clinical"]
    assert variants[0].text == "primary text"


def test_load_variants_skips_missing(tmp_path: Path) -> None:
    primary = tmp_path / "resume.txt"
    primary.write_text("primary")
    missing = tmp_path / "nope.txt"

    def _loader(p: object) -> str:
        path = Path(p)  # type: ignore[arg-type]
        if not path.exists():
            raise FileNotFoundError(str(path))
        return path.read_text()

    variants = load_variants(primary, [missing], loader=_loader)
    assert len(variants) == 1
    assert variants[0].label == "resume"
