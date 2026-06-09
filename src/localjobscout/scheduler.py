from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, cast

import schedule

from localjobscout import db, matcher, notifier, prefilter, resume
from localjobscout.alerts import AlertSender, EmailAlertSender
from localjobscout.config import Settings
from localjobscout.db import Job
from localjobscout.dedup import compute_job_hash, deduplicate
from localjobscout.matching import (
    JobFilter,
    extract_deadline,
    extract_salary_from_text,
    extract_skills,
)
from localjobscout.scrapers.adzuna import AdzunaScraper
from localjobscout.scrapers.base import Scraper
from localjobscout.scrapers.cambridge import CambridgeScraper
from localjobscout.scrapers.conestoga import ConestogaScraper
from localjobscout.scrapers.grandriver import GrandRiverScraper
from localjobscout.scrapers.hamiltonhealth import HamiltonHealthScraper
from localjobscout.scrapers.icims import ICIMSScraper
from localjobscout.scrapers.indeed_pw import IndeedPlaywrightScraper
from localjobscout.scrapers.jobbank import JobBankScraper
from localjobscout.scrapers.laurier import LaurierScraper
from localjobscout.scrapers.linkedin_pw import LinkedInPlaywrightScraper
from localjobscout.scrapers.remoteok import RemoteOKScraper
from localjobscout.scrapers.stmarys import StMarysScraper
from localjobscout.scrapers.talent import TalentScraper
from localjobscout.scrapers.uoguelph import UofGScraper
from localjobscout.scrapers.uwaterloo import UWaterlooScraper

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanResult:
    scrapers_run: int
    jobs_seen: int        # total returned from scrapers
    jobs_new: int         # newly inserted (upsert returned True)
    jobs_excluded: int    # filtered out before scoring (credential mismatch etc.)
    jobs_notified: int    # passed threshold and notification fired
    errors: int           # scrapers that raised
    resumes_tailored: int = 0  # tailored resumes built for top matches


def _make_scrapers(settings: Settings) -> list[Scraper]:
    scrapers: list[Scraper] = []
    if settings.scrapers.jobbank.enabled:
        scrapers.append(
            JobBankScraper(
                max_pages=settings.scrapers.jobbank.max_pages,
                query=settings.scrapers.jobbank.query,
            )
        )
    if settings.scrapers.remoteok.enabled:
        scrapers.append(RemoteOKScraper())
    if settings.scrapers.adzuna.enabled and settings.adzuna_app_id:
        adzuna_locs = settings.scrapers.adzuna.locations
        scrapers.append(
            AdzunaScraper(
                app_id=settings.adzuna_app_id,
                app_key=settings.adzuna_app_key,
                query=settings.scrapers.adzuna.query,
                max_pages=settings.scrapers.adzuna.max_pages,
                location_override=adzuna_locs[0] if adzuna_locs else "",
            )
        )
    if settings.scrapers.linkedin.enabled:
        scrapers.append(
            LinkedInPlaywrightScraper(
                query=settings.scrapers.linkedin.query,
                max_pages=settings.scrapers.linkedin.max_pages,
            )
        )
    if settings.scrapers.indeed.enabled:
        scrapers.append(
            IndeedPlaywrightScraper(
                query=settings.scrapers.indeed.query,
                max_pages=settings.scrapers.indeed.max_pages,
            )
        )
    if settings.scrapers.uoguelph.enabled:
        scrapers.append(UofGScraper(max_pages=settings.scrapers.uoguelph.max_pages))
    if settings.scrapers.uwaterloo.enabled:
        scrapers.append(
            UWaterlooScraper(
                max_pages=settings.scrapers.uwaterloo.max_pages,
                query=settings.scrapers.uwaterloo.query or "research assistant",
            )
        )
    if settings.scrapers.conestoga.enabled:
        scrapers.append(
            ConestogaScraper(max_pages=settings.scrapers.conestoga.max_pages)
        )
    if settings.scrapers.laurier.enabled:
        scrapers.append(
            LaurierScraper(max_pages=settings.scrapers.laurier.max_pages)
        )
    if settings.scrapers.hamiltonhealth.enabled:
        scrapers.append(
            HamiltonHealthScraper(
                max_pages=settings.scrapers.hamiltonhealth.max_pages
            )
        )
    if settings.scrapers.grandriver.enabled:
        scrapers.append(
            GrandRiverScraper(max_pages=settings.scrapers.grandriver.max_pages)
        )
    if settings.scrapers.stmarys.enabled:
        scrapers.append(
            StMarysScraper(max_pages=settings.scrapers.stmarys.max_pages)
        )
    if settings.scrapers.cambridge.enabled:
        scrapers.append(
            CambridgeScraper(max_pages=settings.scrapers.cambridge.max_pages)
        )
    for site in settings.scrapers.icims_sites:
        if site.enabled:
            scrapers.append(
                ICIMSScraper(
                    name=site.name,
                    subdomain=site.subdomain,
                    company=site.company,
                    city=site.city,
                    max_pages=site.max_pages,
                )
            )
    if settings.scrapers.talent.enabled:
        scrapers.append(
            TalentScraper(
                query=settings.scrapers.talent.query or "healthcare",
                max_pages=settings.scrapers.talent.max_pages,
                location=(
                    settings.scrapers.talent.locations[0]
                    if settings.scrapers.talent.locations
                    else ""
                ),
            )
        )
    return scrapers


def _enrich_job(job: Job) -> Job:
    """Extract skills, parse salary, compute dedup hash — returns mutated job."""
    job.skills = extract_skills(job.title, job.description, job.company)

    # Parse salary from the description when the scraper didn't supply one.
    if job.salary_min is None and job.salary_max is None:
        sal_min, sal_max = extract_salary_from_text(job.description)
        job.salary_min = sal_min
        job.salary_max = sal_max

    # Parse an application deadline from the description if present.
    if job.deadline is None:
        job.deadline = extract_deadline(job.description)

    job.job_hash = compute_job_hash(job)
    return job


def _build_alert_sender(settings: Settings) -> AlertSender | None:
    if not settings.alerts.enabled:
        return None
    if settings.alerts.method == "email":
        if not settings.alerts.email_from or not settings.alerts.email_to:
            log.warning("Alert method=email but email_from/email_to not configured")
            return None
        return EmailAlertSender(
            smtp_host=settings.alerts.email_smtp_host,
            smtp_port=settings.alerts.email_smtp_port,
            from_addr=settings.alerts.email_from,
            to_addrs=settings.alerts.email_to,
            password=settings.alerts.email_password or None,
        )
    log.warning("Unknown alert method: %s", settings.alerts.method)
    return None


def _run_inline_suitability(
    settings: Settings,
    resume_text: str,
    scoreable_jobs: list[Job],
) -> None:
    """Score top new above-threshold jobs for suitability using prompt caching.

    Called inline during each scan. Capped at ``inline_suitability_limit`` to
    avoid slowing down scans. Results are cached in DB so they won't be re-scored.
    """
    from localjobscout import suitability as suit_module
    from localjobscout.profile import load_or_parse

    candidates = sorted(
        [j for j in scoreable_jobs if (j.score or 0.0) >= settings.match_threshold],
        key=lambda j: j.score or 0.0,
        reverse=True,
    )[:settings.inline_suitability_limit]

    if not candidates:
        return

    profile = load_or_parse(settings.resume_path, resume_text)
    scored_count = 0
    for job in candidates:
        result = suit_module.score_and_cache(job, resume_text, profile=profile)
        if result is not None:
            scored_count += 1

    if scored_count:
        log.debug("Inline suitability: scored %d new job(s)", scored_count)


def _run_auto_tailor(settings: Settings) -> int:
    """Build gap analysis + a tailored resume for the top_n queue matches.

    Runs at the end of each scan. Gap analysis is DB-cached (an API call only
    fires for uncached jobs, and only when a key/CLI backend is available);
    tailoring itself is fully deterministic and never fabricates. Resumes that
    fail master validation are skipped, not written. Returns the count written.
    """
    from localjobscout import gap as gap_module
    from localjobscout import tailor_resume as tr
    from localjobscout.master_resume import MasterResumeError, load_master

    try:
        master = load_master(settings.resume.master_path)
    except MasterResumeError as exc:
        log.debug("auto-tailor skipped: master unavailable (%s)", exc)
        return 0

    master_hash = master.master_hash()
    candidates = db.get_manual_queue_jobs(settings.match_threshold)
    candidates = sorted(
        candidates, key=lambda j: j.score or 0.0, reverse=True
    )[: max(0, settings.tailor.top_n)]
    if not candidates:
        return 0

    out_dir = settings.db_path.parent / "applications"
    written = 0
    for job in candidates:
        gap_report = None
        cached = db.get_gap_report(job.id, master_hash)
        if cached:
            try:
                gap_report = gap_module.GapReport.from_json(cached)
            except (ValueError, KeyError):
                gap_report = None
        else:
            gap_report = gap_module.analyze_and_cache(job, master)

        resume_obj = tr.build(job, master, settings, gap=gap_report)
        warnings = tr.validate_tailored(
            resume_obj,
            master,
            extra_forbidden=settings.cover_letter.forbidden_claims,
        )
        if warnings:
            log.warning(
                "auto-tailor: rejected resume for %r (off-master: %s)",
                job.title, "; ".join(warnings[:2]),
            )
            continue
        try:
            tr.save(job, resume_obj, out_dir)
            written += 1
        except Exception as exc:  # noqa: BLE001 — never let one job break the scan
            log.warning("auto-tailor: could not save resume for %r: %s",
                        job.title, exc)
    if written:
        log.debug("auto-tailor: built %d tailored resume(s)", written)
    return written


async def run_scan(settings: Settings) -> ScanResult:
    try:
        try:
            resume_text = resume.load_resume(settings.resume_path)
        except FileNotFoundError:
            log.error("Resume not found at %s", settings.resume_path)
            return ScanResult(
                scrapers_run=0, jobs_seen=0, jobs_new=0,
                jobs_excluded=0, jobs_notified=0, errors=1,
            )

        try:
            nlp = resume.get_nlp()
        except RuntimeError as exc:
            log.error("Failed to load spaCy model: %s", exc)
            return ScanResult(
                scrapers_run=0, jobs_seen=0, jobs_new=0,
                jobs_excluded=0, jobs_notified=0, errors=1,
            )

        job_matcher = matcher.build_matcher(settings, nlp)
        db.init_db(settings.db_path)

        scrapers = _make_scrapers(settings)
        scrapers_run = len(scrapers)
        errors = 0
        all_jobs: list[Job] = []

        raw = cast(
            list[list[Job] | BaseException],
            await asyncio.gather(
                *[s.fetch(settings.location) for s in scrapers],
                return_exceptions=True,
            ),
        )
        for i, result in enumerate(raw):
            if isinstance(result, BaseException):
                log.error("Scraper %r raised: %s", scrapers[i].name, result)
                errors += 1
            else:
                all_jobs.extend(result)

        # Enrich + deduplicate before DB insertion
        for job in all_jobs:
            _enrich_job(job)
        all_jobs = deduplicate(all_jobs)

        jobs_seen = len(all_jobs)

        cross_dupes = 0
        new_jobs: list[Job] = []
        for job in all_jobs:
            if job.job_hash and db.hash_exists_in_db(job.job_hash):
                cross_dupes += 1
                continue
            if db.upsert_job(job):
                new_jobs.append(job)
        jobs_new = len(new_jobs)
        if cross_dupes:
            log.debug("Cross-source dedup removed %d duplicate(s)", cross_dupes)

        jobs_excluded = 0
        scoreable_jobs: list[Job] = []
        for job in new_jobs:
            excluded, reason = prefilter.should_exclude(job, settings.prefilter)
            if excluded:
                log.info("Excluded %r: %s", job.title, reason)
                db.update_score(job.id, -1.0)
                jobs_excluded += 1
            else:
                scoreable_jobs.append(job)

        if scoreable_jobs:
            scored = job_matcher.score_jobs(resume_text, scoreable_jobs)
            for job, score in scored:
                job.score = score
                db.update_score(job.id, score)

        # Inline suitability scoring for top new jobs above threshold
        from localjobscout.llm_backend import use_cli

        if (
            settings.inline_suitability
            and (os.environ.get("ANTHROPIC_API_KEY") or use_cli())
            and scoreable_jobs
        ):
            _run_inline_suitability(settings, resume_text, scoreable_jobs)

        jobs_notified = 0
        for job in db.get_unnotified_above(settings.match_threshold):
            notifier.notify_match(job, job.score or 0.0)
            db.mark_notified(job.id)
            jobs_notified += 1

        # Auto-tailor gap analysis + resume for the top matches in the queue.
        resumes_tailored = 0
        if settings.tailor.auto:
            resumes_tailored = _run_auto_tailor(settings)

        # Filter-based alert (salary/skills/keyword matching)
        job_filter = JobFilter(
            min_salary=settings.matching.min_salary,
            max_salary=settings.matching.max_salary,
            required_skills=settings.matching.required_skills,
            excluded_skills=settings.matching.excluded_skills,
            allowed_job_types=settings.matching.allowed_job_types,
            excluded_keywords=settings.matching.excluded_keywords,
        )
        alert_sender = _build_alert_sender(settings)
        if alert_sender is not None and new_jobs:
            matching_jobs = [j for j in new_jobs if job_filter.matches(j)]
            if len(matching_jobs) >= settings.alerts.min_matches_to_alert:
                sent = alert_sender.send(matching_jobs, job_filter)
                if sent:
                    db.log_alert(
                        matched_count=len(matching_jobs),
                        job_ids=[j.id for j in matching_jobs],
                        filter_used=json.dumps(job_filter.model_dump()),
                    )

        scan_result = ScanResult(
            scrapers_run=scrapers_run,
            jobs_seen=jobs_seen,
            jobs_new=jobs_new,
            jobs_excluded=jobs_excluded,
            jobs_notified=jobs_notified,
            errors=errors,
            resumes_tailored=resumes_tailored,
        )
        log.info(
            "Scan complete: %d scrapers, %d seen, %d new, "
            "%d excluded, %d notified, %d tailored, %d errors",
            scrapers_run,
            jobs_seen,
            jobs_new,
            jobs_excluded,
            jobs_notified,
            resumes_tailored,
            errors,
        )
        return scan_result

    except Exception:
        log.exception("Unexpected error in run_scan")
        return ScanResult(
            scrapers_run=0, jobs_seen=0, jobs_new=0,
            jobs_excluded=0, jobs_notified=0, errors=1,
        )


def run_forever(settings: Settings) -> None:
    from datetime import datetime as _dt

    from rich.console import Console
    from rich.panel import Panel

    console = Console()
    console.print(
        Panel(
            f"[bold cyan]LocalJobScout[/bold cyan] — scanning every "
            f"[bold]{settings.scan_interval_minutes}[/bold] min\n"
            f"[dim]Sources: jobbank · adzuna · linkedin · indeed · remoteok · "
            f"uoguelph · uwaterloo · conestoga · laurier · "
            f"grandriver · stmarys · cambridge · hamiltonhealth[/dim]",
            border_style="cyan",
        )
    )

    def _job() -> None:
        with console.status(
            "[cyan]Scanning job boards…[/cyan]", spinner="dots"
        ):
            result = asyncio.run(run_scan(settings))
        _print_scan_panel(console, result)
        try:
            from localjobscout.export import write_matches_md
            md_path = write_matches_md(settings)
            console.print(f"[dim]Matches saved → {md_path}[/dim]")
        except Exception:
            pass
        next_run = _dt.now().strftime("%H:%M")
        console.print(
            f"[dim]Next scan in {settings.scan_interval_minutes} min "
            f"(started at {next_run})[/dim]\n"
        )

    schedule.every(settings.scan_interval_minutes).minutes.do(_job)
    _job()

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        log.info("Scheduler stopped by user")


def _print_scan_panel(console: Any, result: ScanResult) -> None:
    from rich.panel import Panel
    from rich.table import Table

    ok = result.errors == 0
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", min_width=14)
    grid.add_column(style="bold")
    grid.add_row("Scrapers", str(result.scrapers_run))
    grid.add_row("Seen", str(result.jobs_seen))
    new_str = (
        f"[green]{result.jobs_new}[/green]"
        if result.jobs_new
        else "0"
    )
    grid.add_row("New", new_str)
    grid.add_row("Excluded", str(result.jobs_excluded))
    notif_str = (
        f"[bold green]{result.jobs_notified} match(es)![/bold green]"
        if result.jobs_notified
        else "0"
    )
    grid.add_row("Notified", notif_str)
    if result.resumes_tailored:
        grid.add_row("Tailored", f"[cyan]{result.resumes_tailored} resume(s)[/cyan]")
    if result.errors:
        grid.add_row("Errors", f"[red]{result.errors}[/red]")
    title = (
        "[green]✓ Scan complete[/green]"
        if ok
        else "[yellow]⚠ Scan complete (with errors)[/yellow]"
    )
    console.print(Panel(grid, title=title, border_style="green" if ok else "yellow"))
