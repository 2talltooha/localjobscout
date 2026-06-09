from __future__ import annotations

from datetime import UTC, datetime

import pytest

from localjobscout import digest as digest_module
from localjobscout.config import AlertConfig, ScrapersConfig, Settings
from localjobscout.db import Job, make_job_id
from localjobscout.digest import (
    build_digest_html,
    build_digest_text,
    cutoff_iso,
    select_digest_jobs,
    send_digest,
)


def _job(
    title: str,
    *,
    score: float = 0.5,
    first_seen: str = "2026-05-30T00:00:00+00:00",
    status: str | None = None,
    deadline: str | None = None,
) -> Job:
    url = f"https://x.com/{title.replace(' ', '-')}"
    return Job(
        id=make_job_id("t", url),
        source="t",
        title=title,
        url=url,
        description="",
        company="Acme",
        location="Waterloo, ON",
        score=score,
        first_seen=first_seen,
        application_status=status,
        deadline=deadline,
    )


# ─── cutoff_iso ──────────────────────────────────────────────────────────────


def test_cutoff_iso_subtracts_days() -> None:
    now = datetime(2026, 5, 31, tzinfo=UTC)
    assert cutoff_iso(7, now=now).startswith("2026-05-24")


# ─── select_digest_jobs ──────────────────────────────────────────────────────


def test_select_filters_below_threshold() -> None:
    jobs = [_job("Low", score=0.1), _job("High", score=0.8)]
    out = select_digest_jobs(jobs, since_iso="2026-01-01", threshold=0.22, top_n=10)
    assert [j.title for j in out] == ["High"]


def test_select_sorts_by_score_desc() -> None:
    jobs = [_job("Mid", score=0.4), _job("Top", score=0.9), _job("Low", score=0.3)]
    out = select_digest_jobs(jobs, since_iso="2026-01-01", threshold=0.22, top_n=10)
    assert [j.title for j in out] == ["Top", "Mid", "Low"]


def test_select_respects_top_n() -> None:
    jobs = [_job(f"J{i}", score=0.5 + i / 100) for i in range(20)]
    out = select_digest_jobs(jobs, since_iso="2026-01-01", threshold=0.22, top_n=5)
    assert len(out) == 5


def test_select_filters_by_since() -> None:
    jobs = [
        _job("Old", first_seen="2026-01-01T00:00:00+00:00"),
        _job("New", first_seen="2026-05-30T00:00:00+00:00"),
    ]
    out = select_digest_jobs(
        jobs, since_iso="2026-05-01T00:00:00+00:00", threshold=0.22, top_n=10
    )
    assert [j.title for j in out] == ["New"]


def test_select_excludes_terminal_statuses() -> None:
    jobs = [
        _job("Applied", status="applied"),
        _job("Hidden", status="hidden"),
        _job("Rejected", status="rejected"),
        _job("Open", status=None),
        _job("Interested", status="interested"),
    ]
    out = select_digest_jobs(jobs, since_iso="2026-01-01", threshold=0.22, top_n=10)
    titles = {j.title for j in out}
    assert titles == {"Open", "Interested"}


# ─── body builders ───────────────────────────────────────────────────────────


def test_html_contains_jobs_and_links() -> None:
    jobs = [_job("Lab Assistant", deadline="2026-06-01")]
    html = build_digest_html(jobs, "last 7 days")
    assert "Lab Assistant" in html
    assert "https://x.com/Lab-Assistant" in html
    assert "2026-06-01" in html
    assert "<table>" in html


def test_text_contains_jobs() -> None:
    jobs = [_job("Lab Assistant", score=0.75)]
    text = build_digest_text(jobs, "last 7 days")
    assert "Lab Assistant" in text
    assert "0.75" in text
    assert "https://x.com/Lab-Assistant" in text


# ─── send_digest ─────────────────────────────────────────────────────────────


class _FakeSMTP:
    instances: list[_FakeSMTP] = []

    def __init__(self, host: str, port: int, timeout: int | None = None) -> None:
        self.sent: list[tuple[str, list[str], str]] = []
        _FakeSMTP.instances.append(self)

    def __enter__(self) -> _FakeSMTP:
        return self

    def __exit__(self, *a: object) -> None:
        pass

    def ehlo(self) -> None:
        pass

    def starttls(self) -> None:
        pass

    def login(self, user: str, pw: str) -> None:
        pass

    def sendmail(self, frm: str, to: list[str], msg: str) -> None:
        self.sent.append((frm, to, msg))


def _settings(**alert_kwargs: object) -> Settings:
    return Settings(
        scrapers=ScrapersConfig(),
        alerts=AlertConfig(**alert_kwargs),  # type: ignore[arg-type]
    )


def test_send_digest_no_recipients_returns_false() -> None:
    settings = _settings()  # no email_to
    assert send_digest([_job("X")], settings, "last 7 days") is False


def test_send_digest_empty_jobs_returns_true() -> None:
    settings = _settings(email_from="me@x.com", email_to=["you@x.com"])
    assert send_digest([], settings, "last 7 days") is True


def test_send_digest_sends_email(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeSMTP.instances = []
    monkeypatch.setattr(digest_module.smtplib, "SMTP", _FakeSMTP)
    settings = _settings(
        email_from="me@x.com",
        email_to=["you@x.com"],
        email_password="pw",
    )
    ok = send_digest([_job("Lab Assistant")], settings, "last 7 days")
    assert ok is True
    assert len(_FakeSMTP.instances) == 1
    frm, to, raw = _FakeSMTP.instances[0].sent[0]
    assert to == ["you@x.com"]

    import email
    from email.header import make_header
    parsed = email.message_from_string(raw)
    subject = str(make_header(email.header.decode_header(parsed["Subject"])))
    assert "LocalJobScout digest" in subject
