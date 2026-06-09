from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table

from localjobscout import db as db_module
from localjobscout import matcher as matcher_module
from localjobscout import report as report_module
from localjobscout import resume as resume_module
from localjobscout import tracker as tracker_module
from localjobscout.config import Settings
from localjobscout.db import APPLICATION_STATUSES, Job, make_job_id
from localjobscout.notifier import check_notifications_available, notify_match
from localjobscout.scheduler import _print_scan_panel, run_forever, run_scan

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

console = Console()
log = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True)],
    )
    # Suppress verbose HTTP/scraper logs — user only needs scan summaries.
    for _noisy in ("httpx", "httpcore", "playwright", "hpack"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)


def _cmd_check() -> int:
    available, hint = check_notifications_available()
    if available:
        console.print("[green]Notifications available.[/green]")
        return 0
    console.print(f"[yellow]Notifications unavailable:[/yellow] {hint}")
    return 1


def _cmd_test_notify() -> int:
    job = Job(
        id=make_job_id("test", "https://example.com/test"),
        source="test",
        title="Test Position",
        company="LocalJobScout",
        location="Guelph, ON",
        url="https://example.com/test",
        description="",
        first_seen=datetime.now(UTC).isoformat(),
    )
    score = 0.42
    suffix = f" ({score:.0%})"
    title_preview = f"New match: {job.title}{suffix}"
    message_preview = (
        f"{job.company or 'Unknown'} — {job.location or 'Unknown'}\n"
        f"{job.url[:80]}"
    )
    console.print("Sending notification …")
    console.print(f"  title:   {title_preview}")
    console.print(f"  message: {message_preview!r}")
    notify_match(job, score)
    console.print("[green]Done.[/green]")
    return 0


def _cmd_diagnose(settings: Settings, all_jobs: bool = False) -> int:
    """Score recent DB jobs against current resume and print a score table.

    When all_jobs=True, scores the entire corpus instead of the most recent 50.
    The displayed table is still capped at 50 rows; percentile stats are
    computed over the full scored set.
    """
    db_module.init_db(settings.db_path)
    jobs = db_module.get_recent_jobs(limit=None if all_jobs else 50)

    if not jobs:
        console.print(
            "[yellow]No jobs in database yet — run --once first.[/yellow]"
        )
        return 0

    try:
        resume_text = resume_module.load_resume(settings.resume_path)
    except FileNotFoundError:
        console.print(
            f"[red]Resume not found at {settings.resume_path}[/red]"
        )
        return 1

    try:
        nlp = resume_module.get_nlp()
    except RuntimeError as exc:
        console.print(f"[red]Failed to load spaCy model: {exc}[/red]")
        return 1

    job_matcher = matcher_module.build_matcher(settings, nlp)
    scored = job_matcher.score_jobs(resume_text, jobs)

    scope = "full corpus" if all_jobs else "50 most recent"
    console.print(
        f"\n[bold]Score Diagnostic[/bold] — freshly computed against "
        f"current resume and config ({scope})\n"
    )

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("#", width=4, justify="right")
    table.add_column("Score", width=6, justify="right")
    table.add_column("✓", width=3, justify="center")
    table.add_column("Title", min_width=30, max_width=38)
    table.add_column("Company", min_width=15, max_width=20)
    table.add_column("Source", width=10)

    threshold = settings.match_threshold
    display_limit = 50
    for rank, (job, score) in enumerate(scored[:display_limit], start=1):
        check = "[green]✓[/green]" if score >= threshold else ""
        title = (job.title[:37] + "…") if len(job.title) > 38 else job.title
        company = (
            (job.company[:19] + "…") if len(job.company) > 20 else job.company
        )
        table.add_row(
            str(rank),
            f"{score:.2f}",
            check,
            title,
            company,
            job.source,
        )

    console.print(table)
    if len(scored) > display_limit:
        console.print(
            f"[dim]... ({len(scored) - display_limit} more rows below the "
            f"top {display_limit} not shown)[/dim]"
        )

    raw = sorted([s for _, s in scored])
    n = len(raw)
    top = raw[-1] if n > 0 else 0.0
    p50 = raw[n // 2] if n > 0 else 0.0
    p75 = raw[int(n * 0.75)] if n > 0 else 0.0
    p90 = raw[int(n * 0.9)] if n > 0 else 0.0
    above = sum(1 for s in raw if s >= threshold)
    console.print(
        f"Threshold: [bold]{threshold:.2f}[/bold] | "
        f"Top: [bold]{top:.2f}[/bold] | "
        f"P90: {p90:.2f} | "
        f"P75: {p75:.2f} | "
        f"P50: {p50:.2f} | "
        f"Above: {above} | "
        f"Scored: {n}\n"
    )
    return 0


def _auto_export(settings: Settings) -> None:
    """Silently regenerate matches.md; called after every scan."""
    try:
        from localjobscout.export import write_matches_md
        path = write_matches_md(settings)
        console.print(f"[dim]Matches → {path}[/dim]")
    except Exception:
        pass


def _cmd_export(settings: Settings) -> int:
    """Write data/matches.md and report to user."""
    from localjobscout.export import write_matches_md
    db_module.init_db(settings.db_path)
    path = write_matches_md(settings)
    console.print(
        Panel(
            f"Wrote [bold]{path}[/bold]\n"
            "[dim]All matched jobs ranked by score. "
            "Open in any editor. "
            "Refreshed automatically after every scan.[/dim]",
            title="[green]✓ Export complete[/green]",
            border_style="green",
        )
    )
    return 0


def main() -> int:
    _configure_logging()
    parser = argparse.ArgumentParser(
        prog="localjobscout",
        description=(
            "Local job matching against scraped postings with OS notifications. "
            "Default: run the scheduler indefinitely. Use --once for a single scan."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check if notifications are available and exit",
    )
    parser.add_argument(
        "--test-notify",
        action="store_true",
        help="Fire a test notification and exit",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        metavar="PATH",
        help="Path to config file (default: ./config.yaml)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single scan and exit",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Score recent DB jobs against current resume and print score table",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="With --diagnose: score the entire corpus, not just the most recent 50",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Generate an HTML job tracker (sorted by match score) and open it",
    )
    parser.add_argument(
        "--report-output",
        type=Path,
        default=Path("data/jobs.html"),
        metavar="PATH",
        help="Output path for the --report HTML (default: data/jobs.html)",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="With --report: write the file but do not launch a browser",
    )
    parser.add_argument(
        "--mark",
        nargs=2,
        metavar=("STATUS", "JOB_ID"),
        help=(
            "Mark JOB_ID (full id or >=4-char prefix) with STATUS: "
            f"{', '.join(APPLICATION_STATUSES)}, or 'clear' to remove "
            "tracking"
        ),
    )
    parser.add_argument(
        "--applications",
        nargs="?",
        const="*",
        metavar="STATUS",
        help=(
            "List tracked applications. Optionally filter by status "
            "(e.g. --applications applied)"
        ),
    )
    parser.add_argument(
        "--cover",
        metavar="JOB_ID",
        help=(
            "Generate a cover letter for JOB_ID (full id or >=4-char "
            "prefix). Uses ANTHROPIC_API_KEY if set, else a template."
        ),
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="Launch the interactive TUI dashboard (recommended).",
    )
    parser.add_argument(
        "--rescore",
        action="store_true",
        help=(
            "Rescore all DB jobs against the current resume.txt. "
            "Run after editing your resume to refresh all scores."
        ),
    )
    parser.add_argument(
        "--prep",
        metavar="JOB_ID",
        help=(
            "Generate interview prep Q&A for JOB_ID (full id or >=4-char "
            "prefix). Uses ANTHROPIC_API_KEY if set."
        ),
    )
    parser.add_argument(
        "--tailor",
        metavar="JOB_ID",
        help=(
            "Build a job-specific resume for JOB_ID from the master resume — "
            "selects/orders real items by profile + gap analysis, renders a "
            "one-page PDF. Validated against the master; never fabricates. "
            "Add --preview to print the selection without writing files."
        ),
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="With --tailor: print the selected items + summary, write nothing.",
    )
    parser.add_argument(
        "--tailor-tips",
        metavar="JOB_ID",
        help=(
            "Generate resume tailoring SUGGESTIONS for JOB_ID (honest reframings, "
            "keywords to add, gaps). Requires ANTHROPIC_API_KEY."
        ),
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help=(
            "Write data/matches.md — ranked list of every job that ever "
            "matched your resume, with application status. Opens in any editor."
        ),
    )
    parser.add_argument(
        "--auto-apply",
        action="store_true",
        help=(
            "Run the auto-apply pipeline: filter for first-year-appropriate jobs, "
            "generate cover letters, and print a plan. "
            "Add --auto-apply-send to actually send email applications."
        ),
    )
    parser.add_argument(
        "--auto-apply-send",
        action="store_true",
        help=(
            "With --auto-apply: actually send email applications and mark "
            "portal jobs as 'interested'. Without this flag, runs dry-run only."
        ),
    )
    parser.add_argument(
        "--auto-apply-limit",
        type=int,
        default=10,
        metavar="N",
        help="Max number of jobs to process in one auto-apply run (default: 10).",
    )
    parser.add_argument(
        "--auto-apply-score",
        type=float,
        default=None,
        metavar="FLOAT",
        help="Override minimum score threshold for auto-apply (e.g. 0.22).",
    )
    parser.add_argument(
        "--yes-send-without-review",
        action="store_true",
        help=(
            "With --auto-apply --auto-apply-send: skip per-job review "
            "(same as setting auto_apply.unattended=true in config.yaml). "
            "Still enforces daily_limit, cover-letter validation, and logs "
            "everything to data/auto_apply_log.jsonl."
        ),
    )
    parser.add_argument(
        "--suitability",
        action="store_true",
        help=(
            "Score DB jobs above threshold for first-year suitability using "
            "the Anthropic API (ANTHROPIC_API_KEY required). "
            "Results are cached per-job in the DB."
        ),
    )
    parser.add_argument(
        "--suitability-limit",
        type=int,
        default=50,
        metavar="N",
        help="Max jobs to suitability-score in one run (default: 50).",
    )
    parser.add_argument(
        "--follow-up",
        action="store_true",
        help="List applied jobs with no status update after 7 days.",
    )
    parser.add_argument(
        "--deadlines",
        action="store_true",
        help=(
            "List jobs with an upcoming application deadline, soonest first. "
            "Expired deadlines are hidden."
        ),
    )
    parser.add_argument(
        "--compare-resumes",
        action="store_true",
        help=(
            "Score the DB corpus against your primary resume plus every file "
            "in resume_variants, and report which resume performs best."
        ),
    )
    parser.add_argument(
        "--digest",
        action="store_true",
        help=(
            "Build a top-N digest of recent matches. Prints to console; "
            "add --digest-send to email it via the alerts SMTP settings."
        ),
    )
    parser.add_argument(
        "--digest-send",
        action="store_true",
        help="With --digest: actually send the digest email (else dry-run).",
    )
    parser.add_argument(
        "--digest-days",
        type=int,
        default=7,
        metavar="N",
        help="With --digest: look back N days for matches (default: 7).",
    )
    parser.add_argument(
        "--digest-top",
        type=int,
        default=10,
        metavar="N",
        help="With --digest: include the top N jobs by score (default: 10).",
    )
    parser.add_argument(
        "--manual-queue",
        action="store_true",
        help=(
            "Show jobs queued for manual submission, sorted by combined score. "
            "Use --open N to open top N URLs in browser."
        ),
    )
    parser.add_argument(
        "--open",
        type=int,
        default=0,
        metavar="N",
        help="With --manual-queue: open top N job URLs in browser (max 10).",
    )
    parser.add_argument(
        "--status",
        metavar="STATUS",
        default=None,
        help=(
            "With --manual-queue: filter by application status "
            "(e.g. interested). Omit to show all non-applied jobs."
        ),
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help=(
            "With --manual-queue: skip the live-check + full-description fetch "
            "(faster, but may show stale/expired postings)."
        ),
    )
    parser.add_argument(
        "--verify-limit",
        type=int,
        default=25,
        metavar="N",
        help=(
            "With --manual-queue: max postings to live-check/enrich per run "
            "(default: 25)."
        ),
    )
    args = parser.parse_args()

    # Settings-independent diagnostic commands run before loading config.
    if args.check:
        return _cmd_check()
    if args.test_notify:
        return _cmd_test_notify()

    config_path: Path = args.config
    if not config_path.exists():
        log.error(
            "Config file not found: %s. Copy config.yaml from the repo to get started.",
            config_path,
        )
        return 1
    settings = Settings.load(config_path)

    if args.tui:
        from localjobscout.tui import launch_tui
        launch_tui(settings)
        return 0

    if args.once:
        with console.status("[cyan]Scanning job boards…[/cyan]", spinner="dots"):
            result = asyncio.run(run_scan(settings))
        _print_scan_panel(console, result)
        _auto_export(settings)
        return 0

    if args.diagnose:
        return _cmd_diagnose(settings, all_jobs=args.all)

    if args.report:
        return _cmd_report(
            settings,
            output_path=args.report_output,
            open_in_browser=not args.no_open,
        )

    if args.mark:
        return _cmd_mark(settings, args.mark[0], args.mark[1])

    if args.applications is not None:
        status = None if args.applications == "*" else args.applications
        return _cmd_applications(settings, status)

    if args.cover:
        return _cmd_cover(settings, args.cover)

    if args.rescore:
        return _cmd_rescore(settings)

    if args.prep:
        return _cmd_prep(settings, args.prep)

    if args.tailor:
        return _cmd_tailor_resume(settings, args.tailor, preview=args.preview)

    if args.tailor_tips:
        return _cmd_tailor_tips(settings, args.tailor_tips)

    if args.export:
        return _cmd_export(settings)

    if args.auto_apply:
        if args.yes_send_without_review:
            settings.auto_apply.unattended = True
        return _cmd_auto_apply(
            settings,
            dry_run=not args.auto_apply_send,
            limit=args.auto_apply_limit,
            min_score=args.auto_apply_score,
        )

    if args.suitability:
        return _cmd_suitability(settings, limit=args.suitability_limit)

    if args.follow_up:
        return _cmd_follow_up(settings)

    if args.deadlines:
        return _cmd_deadlines(settings)

    if args.compare_resumes:
        return _cmd_compare_resumes(settings)

    if args.digest:
        return _cmd_digest(
            settings,
            days=args.digest_days,
            top=args.digest_top,
            send=args.digest_send,
        )

    if args.manual_queue:
        return _cmd_manual_queue(
            settings,
            open_n=min(args.open, 10),
            status_filter=args.status,
            verify=not args.no_verify,
            verify_limit=args.verify_limit,
        )

    run_forever(settings)
    return 0


def _cmd_rescore(settings: Settings) -> int:
    """Rescore all non-excluded DB jobs against the current resume."""
    db_module.init_db(settings.db_path)
    jobs = db_module.get_all_for_rescore()
    if not jobs:
        console.print("[yellow]No jobs to rescore.[/yellow]")
        return 0
    try:
        resume_text = resume_module.load_resume(settings.resume_path)
    except FileNotFoundError:
        console.print(f"[red]Resume not found at {settings.resume_path}[/red]")
        return 1
    try:
        nlp = resume_module.get_nlp()
    except RuntimeError as exc:
        console.print(f"[red]Failed to load spaCy model: {exc}[/red]")
        return 1

    from localjobscout import prefilter as prefilter_module

    job_matcher = matcher_module.build_matcher(settings, nlp)
    with console.status(
        f"[cyan]Rescoring {len(jobs)} jobs against current resume…[/cyan]",
        spinner="dots",
    ):
        # Re-apply prefilter so config changes (new exclude phrases/years) take effect
        excluded = 0
        scoreable: list[Job] = []
        for job in jobs:
            is_excl, _ = prefilter_module.should_exclude(job, settings.prefilter)
            if is_excl:
                db_module.update_score(job.id, -1.0)
                excluded += 1
            else:
                scoreable.append(job)

        scored = job_matcher.score_jobs(resume_text, scoreable)
        for job, score in scored:
            db_module.update_score(job.id, score)

    above = sum(1 for _, s in scored if s >= settings.match_threshold)
    console.print(
        Panel(
            f"Rescored [bold]{len(scored)}[/bold] jobs  ·  "
            f"[dim]{excluded} re-excluded[/dim]\n"
            f"[bold green]{above}[/bold green] above threshold "
            f"([dim]{settings.match_threshold:.2f}[/dim]).",
            title="[green]✓ Rescore complete[/green]",
            border_style="green",
        )
    )
    return 0


def _cmd_prep(settings: Settings, job_id: str) -> int:
    """Generate interview prep Q&A for a job."""
    from localjobscout import prep as prep_module

    db_module.init_db(settings.db_path)
    try:
        path = prep_module.generate_and_save(job_id, settings=settings)
    except prep_module.JobNotFoundError as exc:
        console.print(f"[red]Job not found:[/red] {exc}")
        return 1
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    console.print(f"[green]Wrote interview prep:[/green] {path}")
    return 0


def _cmd_tailor_resume(
    settings: Settings, job_id: str, *, preview: bool = False
) -> int:
    """Build a job-specific resume from the master resume.

    Classifies the job into a profile, selects/orders real master items by tag
    relevance + gap analysis, validates against the master, then (unless
    --preview) renders one-page Markdown + PDF next to the cover letter.
    """
    from localjobscout import gap as gap_module
    from localjobscout import tailor_resume as tr
    from localjobscout import tracker as tracker_module

    db_module.init_db(settings.db_path)

    master = _try_load_master(settings)
    if master is None:
        console.print(
            f"[red]Master resume not available[/red] at "
            f"{settings.resume.master_path}. Create it (see master_resume.py)."
        )
        return 1

    try:
        job = tracker_module.resolve_job(job_id)
    except tracker_module.JobNotFoundError as exc:
        console.print(f"[red]Job not found:[/red] {exc}")
        return 1

    # Use a cached gap report when present; never force a network call here.
    gap_report = None
    cached = db_module.get_gap_report(job.id, master.master_hash())
    if cached:
        try:
            gap_report = gap_module.GapReport.from_json(cached)
        except (ValueError, KeyError):
            gap_report = None

    resume = tr.build(job, master, settings, gap=gap_report)

    warnings = tr.validate_tailored(
        resume, master, extra_forbidden=settings.cover_letter.forbidden_claims
    )
    if warnings:
        console.print(
            "[red]✗ Tailored resume rejected — off-master claims detected:[/red]"
        )
        for w in warnings:
            console.print(f"  [red]⚠[/red] {w}")
        console.print("[dim]Nothing written. Fix the master or tailoring logic.[/dim]")
        return 1

    if preview:
        console.print(
            Panel(
                tr.render_markdown(resume),
                title=f"[cyan]Preview · profile={resume.profile}[/cyan]",
                border_style="cyan",
            )
        )
        console.print("[dim]--preview: no files written.[/dim]")
        return 0

    out_dir = settings.db_path.parent / "applications"
    paths = tr.save(job, resume, out_dir)
    console.print(
        Panel(
            f"[bold]{job.title}[/bold]  ·  {job.company}\n"
            f"Profile: [cyan]{resume.profile}[/cyan]  ·  "
            f"sections: {len(resume.sections)}\n\n"
            f"PDF: {paths['pdf']}\n"
            f"MD:  {paths['markdown']}",
            title="[green]✓ Tailored Resume[/green]",
            border_style="green",
        )
    )
    return 0


def _cmd_tailor_tips(settings: Settings, job_id: str) -> int:
    """Generate resume tailoring suggestions for a specific job."""
    from localjobscout import tailor as tailor_module
    from localjobscout import tracker as tracker_module
    from localjobscout.profile import load_or_parse

    db_module.init_db(settings.db_path)

    try:
        job = tracker_module.resolve_job(job_id)
    except tracker_module.JobNotFoundError as exc:
        console.print(f"[red]Job not found:[/red] {exc}")
        return 1

    try:
        resume_text = resume_module.load_resume(settings.resume_path)
    except FileNotFoundError:
        console.print(f"[red]Resume not found at {settings.resume_path}[/red]")
        return 1

    with console.status("[cyan]Parsing resume profile…[/cyan]", spinner="dots"):
        profile = load_or_parse(settings.resume_path, resume_text)

    with console.status(
        f"[cyan]Generating tailoring suggestions for {job.title[:50]}…[/cyan]",
        spinner="dots",
    ):
        try:
            suggestions = tailor_module.generate_tailoring(job, resume_text, profile)
        except tailor_module.TailoringError as exc:
            console.print(f"[red]Tailoring failed:[/red] {exc}")
            return 1

    output_dir = settings.db_path.parent / "applications"
    path = tailor_module.save_tailoring(job, suggestions, output_dir)

    console.print(
        Panel(
            f"[bold]{job.title}[/bold]  ·  {job.company}\n\n"
            f"{suggestions[:600]}{'…' if len(suggestions) > 600 else ''}\n\n"
            f"[dim]Full report saved to {path}[/dim]",
            title="[green]✓ Resume Tailoring[/green]",
            border_style="green",
        )
    )
    return 0


def _cmd_mark(settings: Settings, status: str, job_id: str) -> int:
    db_module.init_db(settings.db_path)
    target_status: str | None = None if status == "clear" else status
    try:
        job = tracker_module.mark_status(job_id, target_status)
    except tracker_module.JobNotFoundError as exc:
        console.print(f"[red]Job not found:[/red] {exc}")
        return 1
    except tracker_module.InvalidStatusError as exc:
        console.print(f"[red]Invalid status:[/red] {exc}")
        return 1
    label = job.application_status or "cleared"
    console.print(
        f"[green]Marked[/green] {job.id[:8]}… as [bold]{label}[/bold] "
        f"— {job.title} @ {job.company}"
    )
    return 0


def _cmd_applications(settings: Settings, status: str | None) -> int:
    db_module.init_db(settings.db_path)
    jobs = tracker_module.list_applications(status=status)
    if not jobs:
        console.print(
            "[yellow]No tracked applications yet. Use --mark to start.[/yellow]"
        )
        return 0
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Short ID", width=10)
    table.add_column("Status", width=12)
    table.add_column("Applied", width=11)
    table.add_column("Score", width=6, justify="right")
    table.add_column("Title", min_width=30, max_width=44)
    table.add_column("Company", min_width=12, max_width=22)
    table.add_column("Source", width=10)
    for job in jobs:
        applied = (job.applied_at or "")[:10]
        score = f"{job.score:.2f}" if job.score is not None else "—"
        title = (
            (job.title[:43] + "…") if len(job.title) > 44 else job.title
        )
        company = (
            (job.company[:21] + "…") if len(job.company) > 22
            else job.company
        )
        table.add_row(
            job.id[:8],
            job.application_status or "",
            applied,
            score,
            title,
            company,
            job.source,
        )
    console.print(table)
    console.print(f"[dim]{len(jobs)} tracked.[/dim]")
    return 0


def _cmd_cover(settings: Settings, job_id: str) -> int:
    db_module.init_db(settings.db_path)
    try:
        path, backend = tracker_module.generate_cover_letter(
            job_id, settings=settings
        )
    except tracker_module.JobNotFoundError as exc:
        console.print(f"[red]Job not found:[/red] {exc}")
        return 1
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    console.print(
        f"[green]Wrote cover letter:[/green] {path} "
        f"[dim](backend: {backend})[/dim]"
    )
    return 0


def _cmd_report(
    settings: Settings,
    *,
    output_path: Path,
    open_in_browser: bool,
) -> int:
    """Score the full DB and write an HTML report sorted by match score."""
    db_module.init_db(settings.db_path)
    jobs = db_module.get_recent_jobs(limit=None)

    if not jobs:
        console.print(
            "[yellow]No jobs in database yet — run --once first.[/yellow]"
        )
        return 0

    try:
        resume_text = resume_module.load_resume(settings.resume_path)
    except FileNotFoundError:
        console.print(
            f"[red]Resume not found at {settings.resume_path}[/red]"
        )
        return 1

    try:
        nlp = resume_module.get_nlp()
    except RuntimeError as exc:
        console.print(f"[red]Failed to load spaCy model: {exc}[/red]")
        return 1

    job_matcher = matcher_module.build_matcher(settings, nlp)
    scored = job_matcher.score_jobs(resume_text, jobs)

    path = report_module.write_report(
        scored,
        threshold=settings.match_threshold,
        output_path=output_path,
        open_in_browser=open_in_browser,
    )
    console.print(
        f"[green]Wrote report:[/green] {path} "
        f"({len(scored)} jobs, threshold {settings.match_threshold:.3f})"
    )
    return 0


def _cmd_auto_apply(
    settings: Settings,
    *,
    dry_run: bool,
    limit: int,
    min_score: float | None,
) -> int:
    from localjobscout import auto_apply as aa_module
    from localjobscout import resume as resume_module

    db_module.init_db(settings.db_path)

    try:
        resume_text = resume_module.load_resume(settings.resume_path)
    except FileNotFoundError:
        console.print(f"[red]Resume not found at {settings.resume_path}[/red]")
        return 1

    mode_label = "[yellow]DRY RUN[/yellow]" if dry_run else "[green]LIVE[/green]"
    console.print(f"\n[bold]Auto-Apply Pipeline[/bold] — {mode_label}\n")

    with console.status(
        "[cyan]Filtering jobs and generating cover letters…[/cyan]",
        spinner="dots",
    ):
        report = aa_module.auto_apply_batch(
            settings,
            dry_run=dry_run,
            limit=limit,
            min_score=min_score,
            resume_text=resume_text,
        )

    if not report.records:
        console.print(
            "[yellow]No suitable jobs found above threshold.[/yellow] "
            "[dim]Try --auto-apply-score to lower threshold, "
            "or run --once to fetch fresh jobs.[/dim]"
        )
        return 0

    # Results table
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("#", width=3, justify="right")
    table.add_column("Score", width=6, justify="right")
    table.add_column("Method", width=10)
    table.add_column("Sent", width=5, justify="center")
    table.add_column("Title", min_width=28, max_width=38)
    table.add_column("Company", min_width=14, max_width=22)
    table.add_column("Target / URL", min_width=20, max_width=38)

    method_colours = {
        "email": "[green]email[/green]",
        "linkedin": "[blue]linkedin[/blue]",
        "indeed": "[blue]indeed[/blue]",
        "portal": "[dim]portal[/dim]",
        "unknown": "[dim]?[/dim]",
    }

    for i, rec in enumerate(report.records, 1):
        if rec.sent:
            sent_mark = "[green]✓[/green]"
        elif dry_run:
            sent_mark = "[yellow]~[/yellow]"
        elif rec.error:
            sent_mark = "[red]✗[/red]"
        else:
            sent_mark = "[cyan]→[/cyan]"

        target = rec.apply_target or ""
        if target and len(target) > 38:
            target = target[:35] + "…"

        title = (rec.job.title[:37] + "…") if len(rec.job.title) > 38 else rec.job.title
        company = (
            (rec.job.company[:21] + "…") if len(rec.job.company) > 22
            else rec.job.company
        )

        table.add_row(
            str(i),
            f"{rec.job.score:.2f}",
            method_colours.get(rec.method, rec.method),
            sent_mark,
            title,
            company,
            target or "—",
        )

    console.print(table)

    # Show a brief cover letter preview for the top result
    if report.records:
        top = report.records[0]
        preview_lines = [
            ln for ln in top.cover_letter.splitlines() if ln.strip()
        ][:3]
        preview = "\n".join(preview_lines)
        if top.cover_letter_path:
            console.print(
                f"\n[dim]Cover letter preview ({top.job.title}):[/dim]\n"
                f"{preview}\n"
                f"[dim]… saved to {top.cover_letter_path}[/dim]"
            )

    # Summary panel
    dry_hint = (
        f"\n[dim]Run with [bold]--auto-apply-send[/bold] to send "
        f"{report.email_count} email application(s) and queue "
        f"{report.portal_count} portal job(s).[/dim]"
        if dry_run else ""
    )
    smtp_hint = (
        "\n[yellow]Note: SMTP not configured in config.yaml — "
        "email jobs queued as 'interested' instead of sent.[/yellow]"
        if (not dry_run and not settings.auto_apply.smtp_password)
        else ""
    )

    console.print(
        Panel(
            f"Scanned: [bold]{report.candidates_scanned}[/bold] above threshold  ·  "
            f"Filtered unsuitable: [dim]{report.unsuitable}[/dim]\n"
            f"Queued/applied: [bold]{report.applied}[/bold]  ·  "
            f"Email-appliable: [bold]{report.email_count}[/bold]  ·  "
            f"Portal/LinkedIn/Indeed: [bold]{report.portal_count}[/bold]"
            + (f"  ·  Sent: [green]{report.sent_count}[/green]" if not dry_run else "")
            + (f"  ·  Errors: [red]{report.errored}[/red]" if report.errored else "")
            + dry_hint
            + smtp_hint,
            title="[bold]Auto-Apply Summary[/bold]",
            border_style="yellow" if dry_run else "green",
        )
    )
    return 0


def _try_load_master(settings: Settings) -> Any:
    """Load the structured master resume, or None if missing/malformed.

    Tailoring + gap analysis need the master; callers that only want to *show*
    cached results should degrade gracefully when it is absent.
    """
    from localjobscout.master_resume import MasterResumeError, load_master

    try:
        return load_master(settings.resume.master_path)
    except MasterResumeError as exc:
        log.debug("master resume unavailable: %s", exc)
        return None


def _verify_and_enrich_queue(
    settings: Settings, jobs: list[Job], limit: int
) -> tuple[list[Job], dict[str, str]]:
    """Re-fetch up to `limit` candidates: drop confirmed-dead postings, and for
    sources whose stored text is a truncated API snippet, replace it with the
    full posting body and re-run suitability so domain mismatches hidden by the
    truncation get filtered.

    Indeed cannot be checked (Cloudflare blocks both liveness and full-text
    fetch), so its jobs pass through tagged "unverified" — the caller flags them
    so the user knows to confirm closure + requirements manually.

    Returns (surviving jobs, {job_id: "live"|"unverified"})."""
    from localjobscout.auto_apply import check_suitability
    from localjobscout.liveness import verify as verify_live

    kept: list[Job] = []
    confidence: dict[str, str] = {}
    checked = 0
    for job in jobs:
        if checked >= limit:
            kept.append(job)
            confidence[job.id] = "unverified"
            continue
        checked += 1
        result = verify_live(job.url, job.source)
        if result.state == "dead":
            db_module.update_application_status(job.id, "hidden")
            console.print(
                f"[dim]hidden (dead): {job.title[:48]} — {result.reason}[/dim]"
            )
            continue
        if result.full_text and len(result.full_text) > len(job.description or ""):
            # Enrich: persist the fuller text and re-judge on it.
            job.description = result.full_text
            db_module.update_description(job.id, result.full_text)
            verdict = check_suitability(job)
            if not verdict.ok:
                db_module.update_application_status(job.id, "hidden")
                console.print(
                    f"[dim]hidden (full text → unsuitable): "
                    f"{job.title[:42]} — {verdict.reason}[/dim]"
                )
                continue
        confidence[job.id] = "live" if result.state == "live" else "unverified"
        kept.append(job)
    return kept, confidence


def _cmd_manual_queue(
    settings: Settings,
    *,
    open_n: int = 0,
    status_filter: str | None = None,
    verify: bool = True,
    verify_limit: int = 25,
) -> int:
    """Show jobs ready for manual submission, sorted by combined score."""
    import webbrowser
    from datetime import date, timedelta

    from rich.rule import Rule

    from localjobscout.auto_apply import check_suitability
    from localjobscout.prefilter import description_indicates_closed

    db_module.init_db(settings.db_path)

    # Best-effort: load the master resume so we can show cached gap summaries.
    # Never blocks the queue and never makes a network call here — Phase 4's
    # daily flow populates the cache; we only read it.
    master = _try_load_master(settings)
    master_hash = master.master_hash() if master is not None else None

    today = date.today()
    min_date = None
    if settings.queue_max_age_days > 0:
        min_date = (today - timedelta(days=settings.queue_max_age_days)).isoformat()
    raw_jobs = db_module.get_manual_queue_jobs(
        settings.match_threshold,
        status_filter=status_filter,
        today=today.isoformat(),
        min_date=min_date,
    )
    # Static filters: suitability (relevance/credential/title) + closed-phrase.
    jobs = [
        j for j in raw_jobs
        if check_suitability(j).ok
        and not description_indicates_closed(j.description)
    ]

    # Source filter — drop sources that can't be verified (config
    # queue_exclude_sources, e.g. "indeed": Cloudflare blocks all checks).
    bad_sources = {s.lower() for s in settings.queue_exclude_sources}
    if bad_sources:
        jobs = [j for j in jobs if (j.source or "").lower() not in bad_sources]

    # Commute filter — drop locations too far to reach (config geo.exclude_locations).
    far = [c.lower() for c in settings.geo.exclude_locations]
    if far:
        jobs = [
            j for j in jobs
            if not any(f in (j.location or "").lower() for f in far)
        ]

    # University-employer filter — their ATS application flow is unusable, so
    # drop them regardless of which board surfaced the posting.
    uni = [c.lower() for c in settings.prefilter.exclude_companies]
    if uni:
        jobs = [
            j for j in jobs
            if not any(u in (j.company or "").lower() for u in uni)
        ]

    if not jobs:
        label = f" (status={status_filter})" if status_filter else ""
        console.print(
            f"[yellow]No manual-queue candidates{label}.[/yellow] "
            "Run --once to fetch jobs, or --auto-apply to populate the queue."
        )
        return 0

    # Combined score: weighted average of match + suitability (if available)
    def _combined(j: Job) -> float:
        ms = j.score or 0.0
        ss = j.suitability_score
        if ss is not None:
            return ms * 0.6 + ss * 0.4
        return ms

    jobs_sorted = sorted(jobs, key=_combined, reverse=True)

    # Dedup cross-source/branch repeats by (title, company) — keep highest score.
    _seen: set[tuple[str, str]] = set()
    deduped: list[Job] = []
    for j in jobs_sorted:
        key = ((j.title or "").lower().strip(), (j.company or "").lower().strip())
        if key in _seen:
            continue
        _seen.add(key)
        deduped.append(j)
    jobs_sorted = deduped

    # Liveness + full-description enrichment. Re-fetch each candidate once:
    # confirm it is still live, and (for sources whose API truncates, e.g.
    # Adzuna) replace the stored snippet with the full posting text so the
    # suitability filters get a second, better-informed pass. Bounded by
    # --verify-limit; Indeed is skipped (Cloudflare-blocked, unverifiable).
    confidence: dict[str, str] = {}
    if verify:
        jobs_sorted, confidence = _verify_and_enrich_queue(
            settings, jobs_sorted, verify_limit
        )

    if not jobs_sorted:
        console.print(
            "[yellow]No live candidates after verification.[/yellow] "
            "Run --once to fetch fresh postings."
        )
        return 0

    # Resolve cover letter paths
    cl_dir = settings.db_path.parent / "applications"

    status_label = f"  (filter: [bold]{status_filter}[/bold])" if status_filter else ""
    console.print(
        f"\n[bold]Manual Submit Queue[/bold]{status_label} — "
        f"[bold]{len(jobs_sorted)}[/bold] jobs\n"
        "[dim]Mark applied: python -m localjobscout --mark applied <ID>[/dim]\n"
    )

    for rank, job in enumerate(jobs_sorted, 1):
        ms = job.score or 0.0
        ss = job.suitability_score
        combined = _combined(job)

        score_parts = f"match=[bold]{ms:.2f}[/bold]"
        if ss is not None:
            suit_label = job.suitability_reason or ""
            score_parts += (
                f"  suit=[bold]{ss:.2f}[/bold]"
                + (f" [dim]({suit_label[:60]})[/dim]" if suit_label else "")
            )
        score_parts += f"  combined=[bold cyan]{combined:.2f}[/bold cyan]"

        status_str = (
            f"  status=[yellow]{job.application_status}[/yellow]"
            if job.application_status else ""
        )

        # Detect cover letter on disk
        cl_candidates = list(cl_dir.glob(f"{job.source}-{job.id[:8]}*.md"))
        cl_path_str = (
            str(cl_candidates[0]) if cl_candidates else "[dim]no letter yet[/dim]"
        )

        # Detect tailored resume on disk (Phase 4 daily-flow / --tailor output)
        resume_pdf = cl_dir / f"{job.source}-{job.id[:8]}" / "resume.pdf"
        resume_str = (
            f"\nResume: {resume_pdf}"
            if resume_pdf.exists()
            else "\nResume: [dim]not tailored yet[/dim]"
        )

        # Cached gap summary (read-only; no network)
        gap_str = ""
        if master_hash is not None:
            cached = db_module.get_gap_report(job.id, master_hash)
            if cached:
                from localjobscout.gap import GapReport
                try:
                    summary = GapReport.from_json(cached).summary_line()
                    gap_str = f"\nGap:    [dim]{summary}[/dim]"
                except (ValueError, KeyError):
                    gap_str = ""

        method_tag = {
            "linkedin": "[blue]linkedin[/blue]",
            "indeed": "[blue]indeed[/blue]",
        }.get(job.source, f"[dim]{job.source}[/dim]")

        conf = confidence.get(job.id)
        if conf == "live":
            method_tag += "  [green]✓ live[/green]"
        elif conf == "unverified":
            method_tag += (
                "  [yellow]⚠ unverified — confirm it's open & check "
                "experience reqs[/yellow]"
            )

        console.print(Rule(f"[bold]#{rank}[/bold]  {job.id[:8]}", style="dim"))
        console.print(
            f"[bold]{job.title[:70]}[/bold]\n"
            f"[dim]{job.company[:40]}[/dim]  ·  {job.location[:35]}"
            f"  ·  {method_tag}{status_str}\n"
            f"{score_parts}\n"
            f"URL:    {job.url}\n"
            f"Letter: {cl_path_str}"
            f"{resume_str}"
            f"{gap_str}"
        )
        console.print()

    if open_n > 0:
        to_open = jobs_sorted[:open_n]
        console.print(
            f"[cyan]Checking + opening top {len(to_open)} URL(s)…[/cyan]"
        )
        for job in to_open:
            verdict = _verify_job_open(job.url)
            if verdict is False:
                console.print(
                    f"[yellow]Skipped (posting closed):[/yellow] "
                    f"{job.title[:50]} — hidden from future queues."
                )
                db_module.update_application_status(job.id, "hidden")
                continue
            try:
                webbrowser.open(job.url)
            except Exception:
                console.print(f"[red]Could not open:[/red] {job.url}")

    return 0


def _verify_job_open(url: str) -> bool | None:
    """Re-fetch a job URL to check it is still live and accepting applications.

    Returns True (open), False (closed/gone), or None (couldn't determine —
    network error: caller should treat as open rather than hide a good job).
    """
    import httpx

    from localjobscout.prefilter import description_indicates_closed

    try:
        resp = httpx.get(
            url,
            follow_redirects=True,
            timeout=10.0,
            headers={"User-Agent": "Mozilla/5.0 (LocalJobScout)"},
        )
    except Exception:
        return None
    if resp.status_code in (404, 410):
        return False
    if resp.status_code >= 400:
        return None
    return not description_indicates_closed(resp.text)


def _cmd_suitability(settings: Settings, *, limit: int = 50) -> int:
    """Score DB jobs for first-year suitability via Anthropic API."""
    import os

    from localjobscout import suitability as suit_module
    from localjobscout.llm_backend import use_cli

    if not os.environ.get("ANTHROPIC_API_KEY") and not use_cli():
        console.print(
            "[red]ANTHROPIC_API_KEY not set.[/red] "
            "Set it in .env, or set LOCALJOBSCOUT_USE_CLI=1 to use the "
            "claude CLI subscription."
        )
        return 1

    db_module.init_db(settings.db_path)

    try:
        resume_text = resume_module.load_resume(settings.resume_path)
    except FileNotFoundError:
        console.print(f"[red]Resume not found at {settings.resume_path}[/red]")
        return 1

    jobs = db_module.get_jobs_for_suitability(settings.match_threshold, limit=limit)
    if not jobs:
        console.print(
            "[yellow]No un-scored jobs above threshold.[/yellow] "
            "Run --once first or try --rescore."
        )
        return 0

    console.print(
        f"Scoring [bold]{len(jobs)}[/bold] jobs for suitability "
        f"(threshold ≥ {settings.match_threshold:.2f})…\n"
    )

    scored = 0
    for job in jobs:
        result = suit_module.score_and_cache(job, resume_text)
        if result is None:
            continue
        score, reason = result
        scored += 1
        mark = "[green]✓[/green]" if score >= 0.5 else "[dim]✗[/dim]"
        console.print(
            f"  {mark} [bold]{score:.2f}[/bold]  {job.title[:50]}  "
            f"[dim]{reason}[/dim]"
        )

    console.print(f"\n[green]Done.[/green] Scored {scored}/{len(jobs)} jobs.")
    return 0


def _cmd_follow_up(settings: Settings) -> int:
    """List applied jobs with no status change after 7 days."""
    from datetime import datetime as _dt
    from datetime import timedelta

    db_module.init_db(settings.db_path)
    cutoff = (_dt.now(UTC) - timedelta(days=7)).isoformat()

    jobs = db_module.get_applied_jobs(status="applied")
    stale = [j for j in jobs if j.applied_at and j.applied_at < cutoff]

    if not stale:
        console.print(
            "[green]No stale applications.[/green] "
            "All applied jobs < 7 days old."
        )
        return 0

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Short ID", width=10)
    table.add_column("Applied", width=11)
    table.add_column("Days ago", width=9, justify="right")
    table.add_column("Score", width=6, justify="right")
    table.add_column("Title", min_width=30, max_width=44)
    table.add_column("Company", min_width=14, max_width=24)

    now = _dt.now(UTC)
    for job in stale:
        try:
            applied_dt = _dt.fromisoformat(job.applied_at or "")
            days_ago = (now - applied_dt).days
        except ValueError:
            days_ago = 0
        applied = (job.applied_at or "")[:10]
        score = f"{job.score:.2f}" if job.score is not None else "—"
        title = (job.title[:43] + "…") if len(job.title) > 44 else job.title
        company = (job.company[:23] + "…") if len(job.company) > 24 else job.company
        table.add_row(
            job.id[:8],
            applied,
            str(days_ago),
            score,
            title,
            company,
        )

    console.print(table)
    console.print(
        Panel(
            f"[bold]{len(stale)}[/bold] application(s) with no update in > 7 days.\n"
            "[dim]Consider following up or updating status with --mark.[/dim]",
            border_style="yellow",
        )
    )
    return 0


def _cmd_compare_resumes(settings: Settings) -> int:
    """Score the corpus against each resume variant and report the best."""
    from localjobscout import resume_ab

    db_module.init_db(settings.db_path)
    jobs = db_module.get_recent_jobs(limit=None)
    if not jobs:
        console.print(
            "[yellow]No jobs in database yet — run --once first.[/yellow]"
        )
        return 0

    variants = resume_ab.load_variants(
        settings.resume_path, settings.resume_variants
    )
    if len(variants) < 2:
        console.print(
            "[yellow]Need at least 2 resumes to compare.[/yellow] "
            "[dim]Add file paths to resume_variants in config.yaml.[/dim]"
        )
        return 1

    try:
        nlp = resume_module.get_nlp()
    except RuntimeError as exc:
        console.print(f"[red]Failed to load spaCy model: {exc}[/red]")
        return 1

    matcher = matcher_module.build_matcher(settings, nlp)
    labels = [v.label for v in variants]
    with console.status(
        f"[cyan]Scoring {len(jobs)} jobs against {len(variants)} resumes…[/cyan]",
        spinner="dots",
    ):
        results = resume_ab.compare_resumes(matcher, variants, jobs)
        summary = resume_ab.summarize(
            results, labels, threshold=settings.match_threshold
        )

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Resume", min_width=16)
    table.add_column("Avg score", width=10, justify="right")
    table.add_column("Wins", width=6, justify="right")
    table.add_column("Above thr.", width=10, justify="right")
    winner = summary.overall_winner
    for label in labels:
        star = " [green]★[/green]" if label == winner else ""
        table.add_row(
            label + star,
            f"{summary.avg_score.get(label, 0.0):.3f}",
            str(summary.wins.get(label, 0)),
            str(summary.above_threshold.get(label, 0)),
        )
    console.print(f"\n[bold]Resume A/B Comparison[/bold] — {len(jobs)} jobs\n")
    console.print(table)
    console.print(
        Panel(
            f"Overall winner: [bold green]{winner}[/bold green] "
            "(highest average score across the corpus).\n"
            "[dim]Wins = jobs where this resume scored highest. "
            "Set it as resume_path in config.yaml to use it by default.[/dim]",
            border_style="green",
        )
    )
    return 0


def _cmd_digest(
    settings: Settings,
    *,
    days: int,
    top: int,
    send: bool,
) -> int:
    """Build (and optionally email) a top-N digest of recent matches."""
    from localjobscout import digest as digest_module

    db_module.init_db(settings.db_path)
    since = digest_module.cutoff_iso(days)
    all_jobs = db_module.get_recent_jobs(limit=None)
    jobs = digest_module.select_digest_jobs(
        all_jobs,
        since_iso=since,
        threshold=settings.match_threshold,
        top_n=top,
    )
    period_label = f"last {days} day(s)"

    if not jobs:
        console.print(
            f"[yellow]No matches in the {period_label}.[/yellow] "
            "[dim]Run --once to fetch fresh jobs.[/dim]"
        )
        return 0

    # Always show a console preview
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("#", width=3, justify="right")
    table.add_column("Score", width=6, justify="right")
    table.add_column("Title", min_width=28, max_width=42)
    table.add_column("Company", min_width=12, max_width=22)
    table.add_column("Deadline", width=11)
    for i, job in enumerate(jobs, 1):
        score = f"{job.score:.2f}" if job.score is not None else "—"
        title = (job.title[:41] + "…") if len(job.title) > 42 else job.title
        company = (job.company[:21] + "…") if len(job.company) > 22 else job.company
        table.add_row(str(i), score, title, company, job.deadline or "—")
    console.print(f"\n[bold]Digest — top {len(jobs)} ({period_label})[/bold]\n")
    console.print(table)

    if not send:
        console.print(
            "\n[dim]Dry run. Add [bold]--digest-send[/bold] to email this "
            "(requires alerts SMTP settings in config.yaml).[/dim]"
        )
        return 0

    ok = digest_module.send_digest(jobs, settings, period_label)
    if ok:
        console.print(
            f"\n[green]Digest sent[/green] to "
            f"{', '.join(settings.alerts.email_to)}."
        )
        return 0
    console.print(
        "\n[red]Digest not sent.[/red] "
        "[dim]Configure alerts.email_from / email_to / email_password "
        "in config.yaml.[/dim]"
    )
    return 1


def _cmd_deadlines(settings: Settings) -> int:
    """List jobs with an upcoming application deadline, soonest first."""
    from datetime import date

    db_module.init_db(settings.db_path)
    today = date.today().isoformat()
    jobs = db_module.get_jobs_with_deadlines(on_or_after=today)

    if not jobs:
        console.print(
            "[green]No upcoming deadlines.[/green] "
            "[dim]Deadlines are parsed from job descriptions during scan.[/dim]"
        )
        return 0

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Short ID", width=10)
    table.add_column("Deadline", width=11)
    table.add_column("Days left", width=9, justify="right")
    table.add_column("Score", width=6, justify="right")
    table.add_column("Title", min_width=28, max_width=42)
    table.add_column("Company", min_width=12, max_width=22)

    today_d = date.today()
    urgent = 0
    for job in jobs:
        try:
            dl = date.fromisoformat(job.deadline or "")
            days_left = (dl - today_d).days
        except ValueError:
            continue
        days_str = str(days_left)
        if days_left <= 3:
            days_str = f"[red]{days_left}[/red]"
            urgent += 1
        elif days_left <= 7:
            days_str = f"[yellow]{days_left}[/yellow]"
        score = f"{job.score:.2f}" if job.score is not None else "—"
        title = (job.title[:41] + "…") if len(job.title) > 42 else job.title
        company = (job.company[:21] + "…") if len(job.company) > 22 else job.company
        table.add_row(
            job.id[:8],
            job.deadline or "",
            days_str,
            score,
            title,
            company,
        )

    console.print(table)
    console.print(
        Panel(
            f"[bold]{len(jobs)}[/bold] job(s) with upcoming deadlines"
            + (f"  ·  [red]{urgent}[/red] due within 3 days" if urgent else ""),
            border_style="red" if urgent else "cyan",
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
