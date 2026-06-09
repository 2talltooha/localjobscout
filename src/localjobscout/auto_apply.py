"""Auto-apply pipeline for localjobscout.

Workflow
--------
1. Pull scored jobs above combined threshold from DB.
2. Suitability filter (first-year appropriate, Ontario only, no spam/CTF).
3. Detect application method per job: email | linkedin | indeed | portal | unknown.
4. Generate tailored cover letter (Anthropic or template backend).
5. **Validate** letter — flag sentences asserting claims not in resume.
6. Interactive review (default) or unattended mode.
7. For email-method jobs (if sending): SMTP/TLS with resume attached.
8. Portal/LinkedIn/Indeed jobs: save cover letter + open URL in browser.
9. Everything logged to data/auto_apply_log.jsonl.

Safe defaults
-------------
- ``dry_run=True`` — nothing sent, nothing tracked in DB.
- Interactive review is ON unless ``settings.auto_apply.unattended=True``
  AND the ``--auto-apply-send`` flag is used.
- Portal/LinkedIn/Indeed: NEVER automated. Cover letter saved; user opens
  job URL manually (or browser opens automatically in non-dry-run).
"""
from __future__ import annotations

import json
import logging
import re
import smtplib
import webbrowser
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Literal

from localjobscout.config import Settings
from localjobscout.db import Job

logger = logging.getLogger(__name__)

ApplyMethod = Literal["email", "linkedin", "indeed", "portal", "unknown"]

# ─── Suitability constants ────────────────────────────────────────────────────

_SPAM_COMPANIES: frozenset[str] = frozenset({
    "apexfocusgroup",
    "apex focus group",
    "focusgroup",
})

_SENIOR_TITLE_WORDS: tuple[str, ...] = (
    "professor",
    "coordinator",
    "manager",
    "director",
    "sergeant",
    "officer",
    "dean",
    "chair",
    "consultant",
    "specialist",
    "educator",
    "facilitator",
    "lead",
    "senior",
    "principal",
    "vice president",
    "executive",
    "superintendent",
    "supervisor",
    "tenure track",
    "canada research",
    "interim",
)

# Titles that denote a regulated/credentialed profession a first-year bio
# student cannot hold. Used as a title-only guard because Adzuna API
# descriptions are truncated (~500 chars) and usually omit the qualifications
# section, so the description-based credential filter can't see the requirement.
_CREDENTIAL_TITLE_WORDS: tuple[str, ...] = (
    "sonographer",
    "mammographer",
    "technologist",
    "nurse",
    "pmhnp",
    "nurse practitioner",
    "engineer",
    "physician",
    "surgeon",
    "dentist",
    "pharmacist",
    # Ontario pharmacy technicians are OCP-registered (regulated profession);
    # pharmacy student/intern roles require pharmacy-program enrolment.
    "pharmacy technician",
    "pharmacy student",
    "pharmacy intern",
    "dietitian",
    "paramedic",
    "radiographer",
    "psychologist",
    "physiotherapist",
    "anesthesiologist",
    "veterinarian",
    "practitioner",
    "naturopath",
    "naturopathic",
    "therapist",
    "chiropractor",
    "midwife",
    "optometrist",
    "audiologist",
    "psychotherapist",
)

# User preference: not interested in classroom/teaching roles. ("tutor" is kept
# — peer tutoring matches his background.)
_REJECTED_TITLE_WORDS: tuple[str, ...] = (
    "teacher",
    "teaching",
    "instructor",
    "interpreter",
    "translator",
)

_LICENSE_PHRASES: tuple[str, ...] = (
    "registered nurse",
    "r.n. required",
    "rn required",
    "medical laboratory technologist",
    "medical laboratory technician",
    "medical laboratory science",
    "mlt required",
    "mlt diploma",
    "technician diploma",
    "technologist diploma",
    "p.eng",
    "professional engineer required",
    "registered practical nurse",
    "rpn required",
    "must hold a license",
    "licensed pharmacist",
    "registered pharmacy technician",
    "ontario college of pharmacists",
    "registered veterinary technician",
    "class a ",
    "class b ",
    # Professional certification/registration requirements a first-year
    # student cannot hold.
    "certification required",
    "certification is required",
    "must be certified",
    "must be registered with",
    "must be a registered",
    "designation required",
    "cslms",
    "csmls",
    "mlpao",
)

_CTF_POSTING_RE = re.compile(r"^[A-Z]{2,4}\d{3}", re.IGNORECASE)
_EXP_RE = re.compile(
    r"(\d+)\+?\)?\s*(?:years?|yrs?)\s+(?:of\s+)?"
    r"(?:[\w\s,/-]{0,40}?\s+)?experience",
    re.IGNORECASE,
)
# Required completed post-secondary credential a first-year student lacks, e.g.
# "minimum of three (3) years of Community College education".
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
# Graduate / completed-degree standing tokens that appear in job titles.
_DEGREE_STANDING_RE = re.compile(
    r"(?<![a-z])(?:ph\.?\s?d|doctoral|post-?doc|master'?s|m\.?sc|"
    r"graduate student|bs/ms|ms/phd)(?![a-z])",
    re.IGNORECASE,
)

# ─── Data structures ──────────────────────────────────────────────────────────


@dataclass
class SuitResult:
    ok: bool
    reason: str = ""


@dataclass
class ApplyRecord:
    job: Job
    method: ApplyMethod
    apply_target: str | None
    cover_letter: str
    cover_letter_path: Path | None = None
    validation_warnings: list[str] = field(default_factory=list)
    sent: bool = False
    action: str = "pending"  # sent | skipped | manual | error | dry_run
    error: str = ""


@dataclass
class BatchReport:
    candidates_scanned: int = 0
    unsuitable: int = 0
    applied: int = 0
    errored: int = 0
    records: list[ApplyRecord] = field(default_factory=list)

    @property
    def email_count(self) -> int:
        return sum(1 for r in self.records if r.method == "email")

    @property
    def sent_count(self) -> int:
        return sum(1 for r in self.records if r.sent)

    @property
    def portal_count(self) -> int:
        return sum(
            1 for r in self.records
            if r.method in ("portal", "linkedin", "indeed")
        )


# ─── Suitability filter ───────────────────────────────────────────────────────


def check_suitability(job: Job) -> SuitResult:
    """Return whether this job is appropriate for a first-year bio student."""
    company = (job.company or "").lower()
    title = (job.title or "").lower()
    desc = (job.description or "").lower()
    loc = job.location or ""
    combined = f"{title} {desc}"

    for spam in _SPAM_COMPANIES:
        if spam in company:
            return SuitResult(False, f"spam company '{job.company}'")

    if _CTF_POSTING_RE.match(job.title or ""):
        return SuitResult(False, "course-code teaching posting")

    for word in _SENIOR_TITLE_WORDS:
        pattern = r"(?<![a-z])" + re.escape(word) + r"(?![a-z])"
        if re.search(pattern, title):
            return SuitResult(False, f"senior title '{word}'")

    for word in _CREDENTIAL_TITLE_WORDS:
        pattern = r"(?<![a-z])" + re.escape(word) + r"(?![a-z])"
        if re.search(pattern, title):
            return SuitResult(False, f"regulated-profession title '{word}'")

    for word in _REJECTED_TITLE_WORDS:
        pattern = r"(?<![a-z])" + re.escape(word) + r"(?![a-z])"
        if re.search(pattern, title):
            return SuitResult(False, f"declined role type '{word}'")

    # Graduate-standing tokens in the title (e.g. "Student Researcher, PhD",
    # "Research Intern, BS/MS") imply enrolment he does not have. Catches
    # Adzuna rows whose body is truncated so credential_block can't see it.
    if _DEGREE_STANDING_RE.search(job.title or ""):
        return SuitResult(False, "requires graduate/degree standing (title)")

    for phrase in _LICENSE_PHRASES:
        if phrase in combined:
            return SuitResult(False, "requires professional licence/credential")

    # Province filter (redundant with prefilter but catches pre-existing DB rows)
    from localjobscout.prefilter import credential_block, extract_province
    prov = extract_province(loc)
    if prov is not None and prov != "ON":
        return SuitResult(False, f"out-of-province ({prov})")

    years = [int(x.group(1)) for x in _EXP_RE.finditer(combined)]
    if years and max(years) > 2:
        return SuitResult(False, f"requires {max(years)} yrs experience")

    cred_reason = credential_block(combined)
    if cred_reason:
        return SuitResult(False, cred_reason)

    # Pre-med relevance gate — the core purpose of the tool. Drops domain
    # mismatches (civil-eng labs, sales, trades) that only matched on shared
    # vocabulary like "laboratory" or "research".
    from localjobscout.relevance import premed_relevance

    relevant, why = premed_relevance(job.title, job.description)
    if not relevant:
        return SuitResult(False, why)

    return SuitResult(True)


# ─── Method detection ─────────────────────────────────────────────────────────


def _extract_apply_email(text: str) -> str | None:
    for m in _EMAIL_RE.finditer(text):
        addr = m.group(0).lower()
        if any(x in addr for x in ("noreply", "no-reply", "donotreply", "@example")):
            continue
        return addr
    return None


def detect_method(job: Job) -> tuple[ApplyMethod, str | None]:
    desc = job.description or ""
    email = _extract_apply_email(desc)
    if email:
        return "email", email
    if job.source == "linkedin":
        return "linkedin", job.url
    if job.source == "indeed":
        return "indeed", job.url
    if job.source in {
        "uoguelph", "uwaterloo", "laurier", "conestoga",
        "hamiltonhealth", "grandriver", "stmarys", "cambridge", "jobbank",
    }:
        return "portal", job.url
    return "unknown", job.url


# ─── Email sending ────────────────────────────────────────────────────────────


def send_email(
    job: Job,
    cover_letter_text: str,
    *,
    to_addr: str,
    from_addr: str,
    smtp_host: str,
    smtp_port: int,
    smtp_password: str,
    resume_path: Path,
    applicant_name: str,
) -> None:
    subject = f"Application for {job.title} — {applicant_name}"
    msg = MIMEMultipart("mixed")
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    plain = re.sub(r"\*+|#+", "", cover_letter_text)
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    if resume_path.exists():
        with resume_path.open("rb") as fh:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(fh.read())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f'attachment; filename="{resume_path.name}"',
        )
        msg.attach(part)
    with smtplib.SMTP(smtp_host, smtp_port) as srv:
        srv.ehlo()
        srv.starttls()
        srv.login(from_addr, smtp_password)
        srv.sendmail(from_addr, to_addr, msg.as_string())


# ─── Audit log ────────────────────────────────────────────────────────────────


def _append_audit_log(
    log_path: Path,
    rec: ApplyRecord,
    *,
    applicant_name: str,
) -> None:
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "job_id": rec.job.id,
        "title": rec.job.title,
        "company": rec.job.company,
        "method": rec.method,
        "match_score": rec.job.score,
        "suitability_score": rec.job.suitability_score,
        "letter_path": str(rec.cover_letter_path) if rec.cover_letter_path else None,
        "action": rec.action,
        "recipient": rec.apply_target if rec.method == "email" else None,
        "validation_warnings": rec.validation_warnings,
        "error": rec.error or None,
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


# ─── Interactive review ───────────────────────────────────────────────────────


def _interactive_review(rec: ApplyRecord, resume_text: str) -> str:
    """Present one job for user review. Returns action: 'send'|'skip'|'quit'."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.rule import Rule

    console = Console()
    console.print(Rule())
    console.print(
        Panel(
            f"[bold]{rec.job.title}[/bold]\n"
            f"[dim]{rec.job.company}[/dim]  ·  {rec.job.location}\n"
            f"Score: [bold]{rec.job.score:.2f}[/bold]"
            + (
                f"  Suitability: [bold]{rec.job.suitability_score:.2f}[/bold] "
                f"({rec.job.suitability_reason})"
                if rec.job.suitability_score is not None else ""
            )
            + f"\nMethod: [cyan]{rec.method}[/cyan]"
            + (f"  →  {rec.apply_target}" if rec.apply_target else ""),
            title=f"[bold cyan]Job {rec.job.id[:8]}[/bold cyan]",
        )
    )

    if rec.validation_warnings:
        for w in rec.validation_warnings:
            console.print(f"[red]⚠ Validation warning:[/red] {w}")

    console.print("\n[dim]--- Cover Letter Preview ---[/dim]")
    preview_lines = [ln for ln in rec.cover_letter.splitlines() if ln.strip()][:8]
    console.print("\n".join(preview_lines))
    if len([ln for ln in rec.cover_letter.splitlines() if ln.strip()]) > 8:
        console.print("[dim]... (truncated — full letter saved to disk)[/dim]")

    _manual = "[yellow]Manual submit needed[/yellow]"
    method_hint = {
        "email": f"Send email to [green]{rec.apply_target}[/green]",
        "linkedin": f"{_manual} (LinkedIn — will open in browser)",
        "indeed": f"{_manual} (Indeed — will open in browser)",
        "portal": f"{_manual} (portal — will open in browser)",
        "unknown": f"{_manual} (unknown source)",
    }.get(rec.method, "")
    console.print(f"\n{method_hint}")

    console.print(
        "\n[bold]Action:[/bold]  "
        "[s] send / submit    [k] skip    [q] quit"
        + ("    [e] edit letter" if False else "")
    )

    while True:
        try:
            choice = input("  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "quit"
        if choice in ("s", "send"):
            return "send"
        if choice in ("k", "skip", ""):
            return "skip"
        if choice in ("q", "quit"):
            return "quit"
        console.print("[dim]Enter s, k, or q[/dim]")


# ─── Batch runner ─────────────────────────────────────────────────────────────


def auto_apply_batch(
    settings: Settings,
    *,
    dry_run: bool = True,
    limit: int = 10,
    min_score: float | None = None,
    resume_text: str = "",
) -> BatchReport:
    """Run the auto-apply pipeline.

    dry_run=True  — preview only; cover letters saved to disk, nothing sent,
                    no DB writes.
    dry_run=False — interactive review (unless unattended), emails sent for
                    email-method jobs, portal jobs opened in browser.
    """
    from localjobscout import cover_letter as cl_module
    from localjobscout import db as db_module
    from localjobscout import resume as resume_module

    db_module.init_db(settings.db_path)

    threshold = min_score if min_score is not None else max(
        settings.match_threshold, settings.auto_apply.min_score
    )
    report = BatchReport()

    all_jobs = db_module.get_recent_jobs(limit=None)
    candidates = [
        j for j in all_jobs
        if j.score is not None
        and j.score >= threshold
        and j.application_status is None
    ]
    candidates.sort(key=lambda j: j.score or 0.0, reverse=True)
    report.candidates_scanned = len(candidates)

    if not resume_text:
        try:
            resume_text = resume_module.load_resume(settings.resume_path)
        except FileNotFoundError:
            resume_text = ""

    applicant_name = "Taha El Ghadi"
    for line in resume_text.splitlines():
        stripped = line.strip()
        if stripped and 2 <= len(stripped.split()) <= 5:
            applicant_name = stripped
            break

    applications_dir = settings.db_path.parent / "applications"
    applications_dir.mkdir(parents=True, exist_ok=True)
    audit_log_path = settings.db_path.parent / "auto_apply_log.jsonl"

    unattended = settings.auto_apply.unattended and not dry_run
    processed = 0

    for job in candidates:
        if processed >= limit:
            break

        suit = check_suitability(job)
        if not suit.ok:
            report.unsuitable += 1
            logger.debug("unsuitable %s: %s", job.id[:8], suit.reason)
            continue

        method, target = detect_method(job)

        try:
            cl_body, backend = cl_module.generate(
                job, settings=settings, resume_text=resume_text
            )
            cl_full = cl_module.render_markdown(job, cl_body, backend)
        except Exception as exc:
            logger.warning("cover letter failed for %s: %s", job.id[:8], exc)
            report.errored += 1
            continue

        # Validate cover letter — flag false claims (hardcoded + config phrases)
        warnings = cl_module.validate(
            cl_body, resume_text,
            extra_forbidden=settings.cover_letter.forbidden_claims,
        )

        cl_path = applications_dir / f"{job.source}-{job.id[:8]}.md"
        cl_path.write_text(cl_full, encoding="utf-8")

        rec = ApplyRecord(
            job=job,
            method=method,
            apply_target=target,
            cover_letter=cl_body,
            cover_letter_path=cl_path,
            validation_warnings=warnings,
        )

        if dry_run:
            rec.action = "dry_run"
            report.applied += 1
            report.records.append(rec)
            processed += 1
            continue

        # If there are validation warnings, skip in unattended mode
        if warnings and unattended:
            logger.warning(
                "Skipping %s due to validation warnings in unattended mode: %s",
                job.id[:8], "; ".join(warnings),
            )
            rec.action = "skipped"
            _append_audit_log(audit_log_path, rec, applicant_name=applicant_name)
            report.records.append(rec)
            processed += 1
            continue

        # Interactive review (unless unattended)
        if not unattended:
            user_action = _interactive_review(rec, resume_text)
            if user_action == "quit":
                # Don't count this job; just stop
                break
            if user_action == "skip":
                rec.action = "skipped"
                _append_audit_log(audit_log_path, rec, applicant_name=applicant_name)
                report.records.append(rec)
                processed += 1
                continue
            # user_action == "send" — fall through

        # Attempt to act based on method
        if method == "email" and target:
            aa = settings.auto_apply
            if aa.enabled and aa.smtp_password and aa.from_email:
                # Prefer the job-specific tailored resume PDF when one exists,
                # otherwise fall back to the master resume file.
                tailored_pdf = (
                    applications_dir / f"{job.source}-{job.id[:8]}" / "resume.pdf"
                )
                attach_resume = (
                    tailored_pdf if tailored_pdf.exists() else settings.resume_path
                )
                try:
                    send_email(
                        job,
                        cl_body,
                        to_addr=target,
                        from_addr=aa.from_email,
                        smtp_host=aa.smtp_host,
                        smtp_port=aa.smtp_port,
                        smtp_password=aa.smtp_password,
                        resume_path=attach_resume,
                        applicant_name=applicant_name,
                    )
                    rec.sent = True
                    rec.action = "sent"
                    db_module.update_application_status(
                        job.id, "applied",
                        notes=f"auto-emailed to {target}",
                    )
                    db_module.set_cover_letter_path(job.id, str(cl_path))
                    report.applied += 1
                except Exception as exc:
                    rec.error = str(exc)
                    rec.action = "error"
                    report.errored += 1
                    logger.error("email send failed for %s: %s", job.title, exc)
            else:
                # SMTP not configured — queue as interested
                rec.action = "manual"
                db_module.update_application_status(
                    job.id, "interested",
                    notes=f"email apply queued to {target} (SMTP not configured)",
                )
                db_module.set_cover_letter_path(job.id, str(cl_path))
                report.applied += 1
        else:
            # Portal / LinkedIn / Indeed — save cover letter, open browser
            rec.action = "manual"
            db_module.update_application_status(
                job.id, "interested",
                notes=f"cover letter saved, manual submit needed ({method})",
            )
            db_module.set_cover_letter_path(job.id, str(cl_path))
            if target:
                try:
                    webbrowser.open(target)
                except Exception:
                    pass
            report.applied += 1

        _append_audit_log(audit_log_path, rec, applicant_name=applicant_name)
        report.records.append(rec)
        processed += 1

    return report
