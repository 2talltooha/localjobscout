from __future__ import annotations

import json
from pathlib import Path

import pytest

from localjobscout import auto_apply as aa
from localjobscout import db as db_module
from localjobscout.config import (
    AutoApplyConfig,
    CoverLetterConfig,
    FocusConfig,
    ScrapersConfig,
    Settings,
)
from localjobscout.db import Job, make_job_id

# ─── Fixtures / helpers ─────────────────────────────────────────────────────


def _job(
    *,
    title: str = "Lab Assistant",
    company: str = "Acme",
    location: str = "Waterloo, ON",
    description: str = "Entry-level support role.",
    source: str = "jobbank",
    score: float | None = 0.4,
    status: str | None = None,
) -> Job:
    url = f"https://example.com/{source}/{title.replace(' ', '-')}"
    return Job(
        id=make_job_id(source, url),
        source=source,
        title=title,
        url=url,
        description=description,
        company=company,
        location=location,
        score=score,
        application_status=status,
        first_seen="2026-05-31T00:00:00+00:00",
    )


def _settings(tmp_path: Path, **aa_kwargs: object) -> Settings:
    return Settings(
        location="Waterloo, ON",
        match_threshold=0.22,
        scan_interval_minutes=60,
        use_semantic_matcher=False,
        resume_path=tmp_path / "resume.txt",
        db_path=tmp_path / "jobs.db",
        scrapers=ScrapersConfig(),
        focus=FocusConfig(),
        cover_letter=CoverLetterConfig(),
        auto_apply=AutoApplyConfig(**aa_kwargs),  # type: ignore[arg-type]
    )


# ─── check_suitability ──────────────────────────────────────────────────────


def test_suitable_entry_level_job_passes() -> None:
    assert aa.check_suitability(_job()).ok


def test_spam_company_blocked() -> None:
    r = aa.check_suitability(_job(company="ApexFocusGroup LLC"))
    assert not r.ok
    assert "spam" in r.reason


def test_ctf_course_code_blocked() -> None:
    r = aa.check_suitability(_job(title="BIOL230 Teaching Assistant"))
    assert not r.ok
    assert "course-code" in r.reason


@pytest.mark.parametrize(
    "title",
    ["Senior Research Scientist", "Lab Manager", "Department Director",
     "Professor of Biology", "Clinical Supervisor"],
)
def test_senior_titles_blocked(title: str) -> None:
    r = aa.check_suitability(_job(title=title))
    assert not r.ok
    assert "senior title" in r.reason


def test_license_required_blocked() -> None:
    r = aa.check_suitability(
        _job(description="Must be a registered nurse with active license.")
    )
    assert not r.ok
    assert "licence" in r.reason or "credential" in r.reason


@pytest.mark.parametrize(
    "description",
    [
        "Medical Laboratory Technician diploma required.",
        "Current CSLMS or MLPAO certification required.",
        "Registration with CSMLS is required.",
        "Professional certification required for this role.",
        "Candidate must be certified in clinical lab procedures.",
        "Medical laboratory science degree needed.",
    ],
)
def test_credential_required_blocked(description: str) -> None:
    r = aa.check_suitability(_job(description=description))
    assert not r.ok
    assert "credential" in r.reason or "licence" in r.reason


def test_real_clinical_lab_posting_blocked() -> None:
    # The semen-analysis lab posting that slipped through before.
    desc = (
        "Qualifications: At least one (1) year of experience working in "
        "clinical laboratory, specifically in semen analysis. Medical "
        "Laboratory Technician diploma. Current CSLMS or MLPAO "
        "certification required."
    )
    r = aa.check_suitability(_job(title="Laboratory Assistant", description=desc))
    assert not r.ok


def test_out_of_province_blocked() -> None:
    r = aa.check_suitability(_job(location="Vancouver, BC"))
    assert not r.ok
    assert "out-of-province" in r.reason


def test_excessive_experience_blocked() -> None:
    r = aa.check_suitability(
        _job(description="Requires 5 years of relevant experience.")
    )
    assert not r.ok
    assert "yrs experience" in r.reason


def test_two_years_experience_allowed() -> None:
    # cap is >2, so exactly 2 should pass
    assert aa.check_suitability(
        _job(description="2 years experience preferred.")
    ).ok


# ─── detect_method ──────────────────────────────────────────────────────────


def test_detect_email_method() -> None:
    job = _job(description="Send your resume to careers@acme.com today.")
    method, target = aa.detect_method(job)
    assert method == "email"
    assert target == "careers@acme.com"


def test_detect_skips_noreply_email() -> None:
    job = _job(description="Auto: noreply@acme.com. Apply on our portal.",
               source="jobbank")
    method, target = aa.detect_method(job)
    assert method == "portal"  # noreply ignored, falls through to source


def test_detect_linkedin_method() -> None:
    job = _job(source="linkedin", description="Apply via LinkedIn.")
    method, target = aa.detect_method(job)
    assert method == "linkedin"
    assert target == job.url


def test_detect_indeed_method() -> None:
    job = _job(source="indeed", description="Apply on Indeed.")
    method, _ = aa.detect_method(job)
    assert method == "indeed"


def test_detect_portal_method() -> None:
    job = _job(source="uoguelph", description="Apply through our careers site.")
    method, _ = aa.detect_method(job)
    assert method == "portal"


def test_detect_unknown_method() -> None:
    job = _job(source="someweirdsource", description="No email here.")
    method, _ = aa.detect_method(job)
    assert method == "unknown"


# ─── audit log ──────────────────────────────────────────────────────────────


def test_audit_log_writes_jsonl(tmp_path: Path) -> None:
    log_path = tmp_path / "audit.jsonl"
    rec = aa.ApplyRecord(
        job=_job(),
        method="email",
        apply_target="careers@acme.com",
        cover_letter="Dear team,",
        action="sent",
        validation_warnings=["test warning"],
    )
    aa._append_audit_log(log_path, rec, applicant_name="Taha El Ghadi")
    assert log_path.exists()
    entry = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert entry["job_id"] == rec.job.id
    assert entry["method"] == "email"
    assert entry["action"] == "sent"
    assert entry["recipient"] == "careers@acme.com"
    assert entry["validation_warnings"] == ["test warning"]


def test_audit_log_appends(tmp_path: Path) -> None:
    log_path = tmp_path / "audit.jsonl"
    rec = aa.ApplyRecord(
        job=_job(), method="portal", apply_target="https://x.com",
        cover_letter="x", action="manual",
    )
    aa._append_audit_log(log_path, rec, applicant_name="T")
    aa._append_audit_log(log_path, rec, applicant_name="T")
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    # portal method → recipient should be None
    assert json.loads(lines[0])["recipient"] is None


# ─── BatchReport properties ─────────────────────────────────────────────────


def test_batch_report_counts() -> None:
    report = aa.BatchReport()
    report.records = [
        aa.ApplyRecord(job=_job(), method="email", apply_target="a@b.com",
                       cover_letter="x", sent=True),
        aa.ApplyRecord(job=_job(), method="linkedin", apply_target="u",
                       cover_letter="x"),
        aa.ApplyRecord(job=_job(), method="portal", apply_target="u",
                       cover_letter="x"),
    ]
    assert report.email_count == 1
    assert report.sent_count == 1
    assert report.portal_count == 2


# ─── send_email ─────────────────────────────────────────────────────────────


class _FakeSMTP:
    """Records calls; stands in for smtplib.SMTP context manager."""

    instances: list[_FakeSMTP] = []

    def __init__(self, host: str, port: int, timeout: int | None = None) -> None:
        self.host = host
        self.port = port
        self.logged_in = False
        self.sent: list[tuple[str, str, str]] = []
        self.tls = False
        _FakeSMTP.instances.append(self)

    def __enter__(self) -> _FakeSMTP:
        return self

    def __exit__(self, *a: object) -> None:
        pass

    def ehlo(self) -> None:
        pass

    def starttls(self) -> None:
        self.tls = True

    def login(self, user: str, password: str) -> None:
        self.logged_in = True

    def sendmail(self, from_addr: str, to_addr: str, msg: str) -> None:
        self.sent.append((from_addr, to_addr, msg))


def test_send_email_builds_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeSMTP.instances = []
    monkeypatch.setattr(aa.smtplib, "SMTP", _FakeSMTP)

    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 fake resume bytes")

    job = _job(title="Lab Assistant")
    aa.send_email(
        job,
        "Dear team,\n\n**Hello** there.",
        to_addr="careers@acme.com",
        from_addr="me@gmail.com",
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        smtp_password="app-password",
        resume_path=resume,
        applicant_name="Taha El Ghadi",
    )

    assert len(_FakeSMTP.instances) == 1
    srv = _FakeSMTP.instances[0]
    assert srv.tls is True
    assert srv.logged_in is True
    assert len(srv.sent) == 1
    from_addr, to_addr, raw = srv.sent[0]
    assert to_addr == "careers@acme.com"

    # Parse the raw message to inspect decoded headers/body
    import email
    from email.header import decode_header
    parsed = email.message_from_string(raw)
    subject = str(decode_header(parsed["Subject"])[0][0])
    if isinstance(decode_header(parsed["Subject"])[0][0], bytes):
        subject = decode_header(parsed["Subject"])[0][0].decode("utf-8")
    assert "Application for Lab Assistant" in subject

    # resume attached by filename
    assert "resume.pdf" in raw
    # plain text body has markdown stars stripped
    parts = parsed.get_payload()
    plain = parts[0].get_payload(decode=True).decode("utf-8")
    assert "**Hello**" not in plain
    assert "Hello" in plain


def test_send_email_without_resume_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeSMTP.instances = []
    monkeypatch.setattr(aa.smtplib, "SMTP", _FakeSMTP)

    aa.send_email(
        _job(),
        "Dear team,",
        to_addr="careers@acme.com",
        from_addr="me@gmail.com",
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        smtp_password="pw",
        resume_path=tmp_path / "does-not-exist.pdf",
        applicant_name="Taha",
    )
    # still sends, just without attachment
    assert len(_FakeSMTP.instances[0].sent) == 1


# ─── auto_apply_batch (dry run) ─────────────────────────────────────────────


def test_batch_dry_run_no_db_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = _settings(tmp_path)
    settings.resume_path.write_text("Taha El Ghadi\nFirst-year student.")
    db_module.init_db(settings.db_path)

    good = _job(title="Lab Assistant", score=0.5)
    bad = _job(title="Senior Director", company="BigCo", score=0.6)
    db_module.upsert_job(good)
    db_module.upsert_job(bad)

    report = aa.auto_apply_batch(settings, dry_run=True, limit=10)

    assert report.candidates_scanned == 2
    assert report.unsuitable == 1  # senior director filtered
    assert report.applied == 1
    assert report.records[0].action == "dry_run"
    # cover letter saved to disk even in dry run
    assert report.records[0].cover_letter_path is not None
    assert report.records[0].cover_letter_path.exists()
    # DB status untouched in dry run
    refetched = db_module.get_job_by_id(good.id)
    assert refetched is not None
    assert refetched.application_status is None


def test_batch_respects_threshold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = _settings(tmp_path)
    settings.resume_path.write_text("Taha El Ghadi")
    db_module.init_db(settings.db_path)

    db_module.upsert_job(_job(title="Low Score Job", score=0.05))
    report = aa.auto_apply_batch(settings, dry_run=True, min_score=0.22)
    assert report.candidates_scanned == 0


def test_batch_respects_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = _settings(tmp_path)
    settings.resume_path.write_text("Taha El Ghadi")
    db_module.init_db(settings.db_path)

    for i in range(5):
        db_module.upsert_job(
            _job(title=f"Lab Assistant {i}", score=0.5)
        )
    report = aa.auto_apply_batch(settings, dry_run=True, limit=2)
    assert report.applied == 2


def test_batch_skips_already_applied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = _settings(tmp_path)
    settings.resume_path.write_text("Taha El Ghadi")
    db_module.init_db(settings.db_path)

    job = _job(title="Already Applied", score=0.5)
    db_module.upsert_job(job)
    # upsert_job does not persist application_status; set it explicitly
    db_module.update_application_status(job.id, "applied")
    report = aa.auto_apply_batch(settings, dry_run=True)
    # status != None → excluded from candidates
    assert report.candidates_scanned == 0


def test_batch_live_unattended_queues_portal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # prevent real browser opening
    monkeypatch.setattr(aa.webbrowser, "open", lambda *_: True)
    settings = _settings(tmp_path, unattended=True)
    settings.resume_path.write_text("Taha El Ghadi")
    db_module.init_db(settings.db_path)

    job = _job(title="Lab Assistant", source="uoguelph", score=0.5)
    db_module.upsert_job(job)

    report = aa.auto_apply_batch(settings, dry_run=False, limit=5)
    assert report.applied == 1
    rec = report.records[0]
    assert rec.method == "portal"
    assert rec.action == "manual"
    # DB updated to 'interested'
    refetched = db_module.get_job_by_id(job.id)
    assert refetched is not None
    assert refetched.application_status == "interested"
    # audit log written
    audit = settings.db_path.parent / "auto_apply_log.jsonl"
    assert audit.exists()


def test_batch_live_email_sends_when_smtp_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _FakeSMTP.instances = []
    monkeypatch.setattr(aa.smtplib, "SMTP", _FakeSMTP)

    settings = _settings(
        tmp_path,
        enabled=True,
        from_email="me@gmail.com",
        smtp_password="app-pw",
        unattended=True,
    )
    settings.resume_path.write_text("Taha El Ghadi")
    db_module.init_db(settings.db_path)

    job = _job(
        title="Lab Assistant",
        source="jobbank",
        description="Email your resume to hiring@smalllab.ca please.",
        score=0.5,
    )
    db_module.upsert_job(job)

    report = aa.auto_apply_batch(settings, dry_run=False, limit=5)
    assert report.sent_count == 1
    assert len(_FakeSMTP.instances) == 1
    refetched = db_module.get_job_by_id(job.id)
    assert refetched is not None
    assert refetched.application_status == "applied"


def test_batch_email_without_smtp_queues_interested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = _settings(tmp_path, unattended=True)  # no smtp_password
    settings.resume_path.write_text("Taha El Ghadi")
    db_module.init_db(settings.db_path)

    job = _job(
        title="Lab Assistant",
        source="jobbank",
        description="Email hiring@smalllab.ca to apply.",
        score=0.5,
    )
    db_module.upsert_job(job)

    report = aa.auto_apply_batch(settings, dry_run=False, limit=5)
    assert report.sent_count == 0
    refetched = db_module.get_job_by_id(job.id)
    assert refetched is not None
    assert refetched.application_status == "interested"
