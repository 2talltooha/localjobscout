from __future__ import annotations

import smtplib
from unittest.mock import MagicMock, patch

from localjobscout.alerts import EmailAlertSender, build_html_body
from localjobscout.db import Job, make_job_id
from localjobscout.matching import JobFilter


def _job(
    title: str = "Python Developer",
    company: str = "Acme Corp",
    url: str = "https://example.com/job/1",
    salary_min: int | None = 80_000,
    salary_max: int | None = 100_000,
    skills: list[str] | None = None,
    source: str = "indeed",
) -> Job:
    return Job(
        id=make_job_id(source, url),
        source=source,
        title=title,
        url=url,
        description="A great Python role.",
        company=company,
        location="Waterloo, ON",
        posted_at="2025-11-10",
        salary_min=salary_min,
        salary_max=salary_max,
        skills=skills or ["Python", "FastAPI"],
    )


_FILTER = JobFilter(min_salary=70_000, required_skills=["Python"])


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------


def test_build_html_body_contains_title() -> None:
    job = _job()
    html = build_html_body([job], _FILTER, "2025-11-10T12:00:00Z")
    assert "Python Developer" in html


def test_build_html_body_contains_company() -> None:
    job = _job()
    html = build_html_body([job], _FILTER, "2025-11-10T12:00:00Z")
    assert "Acme Corp" in html


def test_build_html_body_contains_salary() -> None:
    job = _job(salary_min=80_000, salary_max=100_000)
    html = build_html_body([job], _FILTER, "2025-11-10T12:00:00Z")
    assert "80,000" in html
    assert "100,000" in html


def test_build_html_body_salary_none() -> None:
    job = _job(salary_min=None, salary_max=None)
    html = build_html_body([job], _FILTER, "2025-11-10T12:00:00Z")
    assert "N/A" in html


def test_build_html_body_contains_job_url() -> None:
    url = "https://indeed.com/viewjob?jk=abc123"
    job = _job(url=url)
    html = build_html_body([job], _FILTER, "2025-11-10T12:00:00Z")
    assert url in html


def test_build_html_body_job_count_in_heading() -> None:
    jobs = [_job(), _job(title="Data Engineer", url="https://example.com/job/2")]
    html = build_html_body(jobs, _FILTER, "2025-11-10T12:00:00Z")
    assert "2 new job" in html


# ---------------------------------------------------------------------------
# EmailAlertSender
# ---------------------------------------------------------------------------


def test_email_sender_no_recipients_returns_false() -> None:
    sender = EmailAlertSender(
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        from_addr="from@example.com",
        to_addrs=[],
    )
    assert sender.send([_job()], _FILTER) is False


def test_email_sender_empty_jobs_returns_true() -> None:
    sender = EmailAlertSender(
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        from_addr="from@example.com",
        to_addrs=["to@example.com"],
    )
    assert sender.send([], _FILTER) is True


def test_email_sender_success() -> None:
    sender = EmailAlertSender(
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        from_addr="from@example.com",
        to_addrs=["to@example.com"],
        password="secret",
    )
    mock_smtp = MagicMock()
    mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
    mock_smtp.__exit__ = MagicMock(return_value=False)

    with patch("smtplib.SMTP", return_value=mock_smtp):
        result = sender.send([_job()], _FILTER)

    assert result is True
    mock_smtp.sendmail.assert_called_once()


def test_email_sender_smtp_exception_returns_false() -> None:
    sender = EmailAlertSender(
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        from_addr="from@example.com",
        to_addrs=["to@example.com"],
    )
    with patch("smtplib.SMTP", side_effect=smtplib.SMTPException("connection error")):
        result = sender.send([_job()], _FILTER)

    assert result is False


def test_email_sender_os_error_returns_false() -> None:
    sender = EmailAlertSender(
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        from_addr="from@example.com",
        to_addrs=["to@example.com"],
    )
    with patch("smtplib.SMTP", side_effect=OSError("network unreachable")):
        result = sender.send([_job()], _FILTER)

    assert result is False
