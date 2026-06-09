from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_mock import MockerFixture

from localjobscout import db
from localjobscout.config import (
    ScraperConfig,
    ScrapersConfig,
    Settings,
    TailorConfig,
)
from localjobscout.db import Job, make_job_id
from localjobscout.scheduler import ScanResult, run_scan

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(
    tmp_path: Path,
    threshold: float = 0.35,
    remoteok_enabled: bool = False,
    linkedin_enabled: bool = False,
) -> Settings:
    resume = tmp_path / "resume.txt"
    resume.write_text("Python developer with five years of backend systems experience")
    return Settings(
        location="Waterloo, ON",
        match_threshold=threshold,
        scan_interval_minutes=60,
        resume_path=resume,
        db_path=tmp_path / "jobs.db",
        # Scan-mechanics tests don't exercise tailoring; keep it off for
        # deterministic ScanResult counts.
        tailor=TailorConfig(auto=False),
        scrapers=ScrapersConfig(
            jobbank=ScraperConfig(enabled=True, max_pages=1),
            remoteok=ScraperConfig(enabled=remoteok_enabled, max_pages=1),
            adzuna=ScraperConfig(enabled=False),
            linkedin=ScraperConfig(enabled=linkedin_enabled, max_pages=1),
            indeed=ScraperConfig(enabled=False),
            uoguelph=ScraperConfig(enabled=False),
            uwaterloo=ScraperConfig(enabled=False),
            conestoga=ScraperConfig(enabled=False),
            laurier=ScraperConfig(enabled=False),
        ),
    )


def _job(title: str) -> Job:
    url = f"https://example.com/jobs/{title.lower().replace(' ', '-')}"
    return Job(
        id=make_job_id("jobbank", url),
        source="jobbank",
        title=title,
        url=url,
        description=f"A role for {title} in Waterloo.",
        company="Acme Corp",
        location="Waterloo, ON",
    )


def _fake_scraper(jobs: list[Job], name: str = "jobbank") -> MagicMock:
    s = MagicMock()
    s.name = name
    s.fetch = AsyncMock(return_value=jobs)
    return s


def _mock_resume(mocker: MockerFixture) -> None:
    mocker.patch("localjobscout.resume.load_resume", return_value="resume text")
    mocker.patch("localjobscout.resume.get_nlp", return_value=MagicMock())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

_MASTER_YAML = """\
contact:
  name: "Taha El Ghadi"
  email: "taha@example.com"
summaries:
  general: "Student who works with people."
  customer-service: "Reliable trilingual camp counselor."
items:
  - id: certs
    section: certs
    core: true
    tags: [certs]
    content:
      title: "Certifications"
      bullets: ["First Aid & CPR."]
  - id: exp-camp
    section: experience
    tags: [customer-service, leadership]
    content:
      title: "Camp Counselor"
      bullets: ["Supervised groups of children."]
"""


def test_run_auto_tailor_writes_resume(tmp_path: Path) -> None:
    from localjobscout import scheduler

    master = tmp_path / "master.yaml"
    master.write_text(_MASTER_YAML, encoding="utf-8")

    settings = _settings(tmp_path)
    settings.resume.master_path = master
    settings.tailor.auto = True

    db.init_db(settings.db_path)
    job = _job("Camp Counselor")
    job.score = 0.6
    db.upsert_job(job)
    db.update_score(job.id, 0.6)

    written = scheduler._run_auto_tailor(settings)
    assert written == 1
    job_dir = settings.db_path.parent / "applications" / f"jobbank-{job.id[:8]}"
    assert (job_dir / "resume.pdf").exists()


def test_run_auto_tailor_skips_when_master_missing(tmp_path: Path) -> None:
    from localjobscout import scheduler

    settings = _settings(tmp_path)
    settings.resume.master_path = tmp_path / "nope.yaml"
    db.init_db(settings.db_path)
    assert scheduler._run_auto_tailor(settings) == 0


@pytest.mark.asyncio
async def test_happy_path_scan(mocker: MockerFixture, tmp_path: Path) -> None:
    """One scraper, 2 jobs, 1 above threshold → 1 notification."""
    settings = _settings(tmp_path, threshold=0.35)
    job_a = _job("Job A")
    job_b = _job("Job B")

    _mock_resume(mocker)
    mocker.patch(
        "localjobscout.scheduler._make_scrapers",
        return_value=[_fake_scraper([job_a, job_b])],
    )
    mock_matcher = MagicMock()
    mock_matcher.score_jobs.return_value = [(job_a, 0.5), (job_b, 0.2)]
    mocker.patch("localjobscout.matcher.build_matcher", return_value=mock_matcher)
    mock_notify = mocker.patch("localjobscout.notifier.notify_match")

    result = await run_scan(settings)

    assert result == ScanResult(
        scrapers_run=1, jobs_seen=2, jobs_new=2,
        jobs_excluded=0, jobs_notified=1, errors=0,
    )
    mock_notify.assert_called_once()


@pytest.mark.asyncio
async def test_scraper_exception_isolated(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """One scraper raises; the other's jobs still process and errors=1."""
    settings = _settings(tmp_path)
    job_a = _job("Job A")

    _mock_resume(mocker)
    bad = MagicMock()
    bad.name = "bad_scraper"
    bad.fetch = AsyncMock(side_effect=RuntimeError("network failure"))
    mocker.patch(
        "localjobscout.scheduler._make_scrapers",
        return_value=[_fake_scraper([job_a]), bad],
    )
    mock_matcher = MagicMock()
    mock_matcher.score_jobs.return_value = [(job_a, 0.1)]
    mocker.patch("localjobscout.matcher.build_matcher", return_value=mock_matcher)
    mocker.patch("localjobscout.notifier.notify_match")

    result = await run_scan(settings)

    assert result.errors == 1
    assert result.jobs_seen == 1
    assert result.jobs_new == 1
    assert result.scrapers_run == 2


@pytest.mark.asyncio
async def test_missing_resume(mocker: MockerFixture, tmp_path: Path) -> None:
    """FileNotFoundError from load_resume → ScanResult(0,0,0,0,1), no crash."""
    settings = _settings(tmp_path)
    mocker.patch(
        "localjobscout.resume.load_resume",
        side_effect=FileNotFoundError("resume not found"),
    )

    result = await run_scan(settings)

    assert result == ScanResult(0, 0, 0, 0, 0, 1)


@pytest.mark.asyncio
async def test_no_new_jobs_no_scoring(mocker: MockerFixture, tmp_path: Path) -> None:
    """Jobs already in DB → upsert returns False → score_jobs never called."""
    settings = _settings(tmp_path)
    job_a = _job("Job A")
    job_b = _job("Job B")

    # Pre-populate so both jobs are already known.
    db.init_db(settings.db_path)
    db.upsert_job(job_a)
    db.upsert_job(job_b)

    _mock_resume(mocker)
    mocker.patch(
        "localjobscout.scheduler._make_scrapers",
        return_value=[_fake_scraper([job_a, job_b])],
    )
    mock_matcher = MagicMock()
    mocker.patch("localjobscout.matcher.build_matcher", return_value=mock_matcher)
    mocker.patch("localjobscout.notifier.notify_match")

    result = await run_scan(settings)

    assert result.jobs_new == 0
    mock_matcher.score_jobs.assert_not_called()


@pytest.mark.asyncio
async def test_below_threshold_no_notification(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Score 0.2 < threshold 0.35 → notifier and mark_notified never called."""
    settings = _settings(tmp_path, threshold=0.35)
    job_a = _job("Job A")

    _mock_resume(mocker)
    mocker.patch(
        "localjobscout.scheduler._make_scrapers",
        return_value=[_fake_scraper([job_a])],
    )
    mock_matcher = MagicMock()
    mock_matcher.score_jobs.return_value = [(job_a, 0.2)]
    mocker.patch("localjobscout.matcher.build_matcher", return_value=mock_matcher)
    mock_notify = mocker.patch("localjobscout.notifier.notify_match")
    mock_mark = mocker.patch("localjobscout.db.mark_notified")

    result = await run_scan(settings)

    assert result.jobs_notified == 0
    mock_notify.assert_not_called()
    mock_mark.assert_not_called()


@pytest.mark.asyncio
async def test_all_scrapers_fail(mocker: MockerFixture, tmp_path: Path) -> None:
    """All scrapers raise → errors == len(scrapers), jobs_seen=0, no crash."""
    settings = _settings(tmp_path)

    _mock_resume(mocker)
    bad1 = MagicMock()
    bad1.name = "scraper1"
    bad1.fetch = AsyncMock(side_effect=RuntimeError("fail 1"))
    bad2 = MagicMock()
    bad2.name = "scraper2"
    bad2.fetch = AsyncMock(side_effect=RuntimeError("fail 2"))
    mocker.patch(
        "localjobscout.scheduler._make_scrapers",
        return_value=[bad1, bad2],
    )
    mock_matcher = MagicMock()
    mocker.patch("localjobscout.matcher.build_matcher", return_value=mock_matcher)

    result = await run_scan(settings)

    assert result.errors == 2
    assert result.jobs_seen == 0
    assert result.scrapers_run == 2


@pytest.mark.asyncio
async def test_both_scrapers_enabled_and_called(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Both scrapers instantiated and fetch called when both enabled in settings."""
    settings = _settings(tmp_path, remoteok_enabled=True)

    _mock_resume(mocker)

    jb_instance = MagicMock()
    jb_instance.name = "jobbank"
    jb_instance.fetch = AsyncMock(return_value=[])
    rok_instance = MagicMock()
    rok_instance.name = "remoteok"
    rok_instance.fetch = AsyncMock(return_value=[])

    mock_jb = mocker.patch(
        "localjobscout.scheduler.JobBankScraper", return_value=jb_instance
    )
    mock_rok = mocker.patch(
        "localjobscout.scheduler.RemoteOKScraper", return_value=rok_instance
    )
    mock_matcher = MagicMock()
    mocker.patch("localjobscout.matcher.build_matcher", return_value=mock_matcher)

    result = await run_scan(settings)

    mock_jb.assert_called_once()
    mock_rok.assert_called_once()
    jb_instance.fetch.assert_called_once_with(settings.location)
    rok_instance.fetch.assert_called_once_with(settings.location)
    assert result.scrapers_run == 2
    assert result.errors == 0


@pytest.mark.asyncio
async def test_three_scrapers_enabled_and_called(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """All three scrapers instantiated and fetch called when all enabled."""
    settings = _settings(tmp_path, remoteok_enabled=True, linkedin_enabled=True)

    _mock_resume(mocker)

    jb_instance = MagicMock()
    jb_instance.name = "jobbank"
    jb_instance.fetch = AsyncMock(return_value=[])
    rok_instance = MagicMock()
    rok_instance.name = "remoteok"
    rok_instance.fetch = AsyncMock(return_value=[])
    li_instance = MagicMock()
    li_instance.name = "linkedin"
    li_instance.fetch = AsyncMock(return_value=[])

    mock_jb = mocker.patch(
        "localjobscout.scheduler.JobBankScraper", return_value=jb_instance
    )
    mock_rok = mocker.patch(
        "localjobscout.scheduler.RemoteOKScraper", return_value=rok_instance
    )
    mock_li = mocker.patch(
        "localjobscout.scheduler.LinkedInPlaywrightScraper",
        return_value=li_instance,
    )
    mock_matcher = MagicMock()
    mocker.patch("localjobscout.matcher.build_matcher", return_value=mock_matcher)

    result = await run_scan(settings)

    mock_jb.assert_called_once()
    mock_rok.assert_called_once()
    mock_li.assert_called_once()
    jb_instance.fetch.assert_called_once_with(settings.location)
    rok_instance.fetch.assert_called_once_with(settings.location)
    li_instance.fetch.assert_called_once_with(settings.location)
    assert result.scrapers_run == 3
    assert result.errors == 0


@pytest.mark.asyncio
async def test_excluded_jobs_not_scored(mocker: MockerFixture, tmp_path: Path) -> None:
    """Prefilter-excluded job gets score=-1.0 and is never passed to matcher."""
    settings = _settings(tmp_path)
    job_excl = _job("Excluded Job")

    _mock_resume(mocker)
    mocker.patch(
        "localjobscout.scheduler._make_scrapers",
        return_value=[_fake_scraper([job_excl])],
    )
    mock_matcher = MagicMock()
    mocker.patch("localjobscout.matcher.build_matcher", return_value=mock_matcher)
    mocker.patch(
        "localjobscout.prefilter.should_exclude",
        return_value=(True, "matched phrase: 'test'"),
    )
    mocker.patch("localjobscout.notifier.notify_match")

    result = await run_scan(settings)

    assert result.jobs_excluded == 1
    assert result.jobs_new == 1
    mock_matcher.score_jobs.assert_not_called()


@pytest.mark.asyncio
async def test_excluded_jobs_receive_score_minus_one(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """DB update_score(-1.0) called for excluded job."""
    settings = _settings(tmp_path)
    job_excl = _job("Excluded Job")

    _mock_resume(mocker)
    mocker.patch(
        "localjobscout.scheduler._make_scrapers",
        return_value=[_fake_scraper([job_excl])],
    )
    mocker.patch("localjobscout.matcher.build_matcher", return_value=MagicMock())
    mocker.patch(
        "localjobscout.prefilter.should_exclude",
        return_value=(True, "matched phrase"),
    )
    mocker.patch("localjobscout.notifier.notify_match")
    mock_update = mocker.patch("localjobscout.db.update_score")

    await run_scan(settings)

    mock_update.assert_called_once_with(job_excl.id, -1.0)


@pytest.mark.asyncio
async def test_no_exclusions_jobs_excluded_zero(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """When prefilter passes all jobs, jobs_excluded=0 and matcher is called."""
    settings = _settings(tmp_path)
    job_a = _job("Job A")

    _mock_resume(mocker)
    mocker.patch(
        "localjobscout.scheduler._make_scrapers",
        return_value=[_fake_scraper([job_a])],
    )
    mock_matcher = MagicMock()
    mock_matcher.score_jobs.return_value = [(job_a, 0.4)]
    mocker.patch("localjobscout.matcher.build_matcher", return_value=mock_matcher)
    mocker.patch("localjobscout.notifier.notify_match")

    result = await run_scan(settings)

    assert result.jobs_excluded == 0
    mock_matcher.score_jobs.assert_called_once()
