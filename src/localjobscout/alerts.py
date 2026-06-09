from __future__ import annotations

import logging
import smtplib
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from localjobscout.db import Job
from localjobscout.matching import JobFilter

logger = logging.getLogger(__name__)


class AlertSender(ABC):
    @abstractmethod
    def send(self, jobs: list[Job], filter_used: JobFilter) -> bool:
        """Send alert with matching jobs. Return True on success."""


def _format_salary(job: Job) -> str:
    if job.salary_min is None and job.salary_max is None:
        return "N/A"
    lo = f"${job.salary_min:,}" if job.salary_min is not None else "?"
    hi = f"${job.salary_max:,}" if job.salary_max is not None else "?"
    if lo == hi:
        return lo
    return f"{lo} – {hi}"


def build_html_body(jobs: list[Job], filter_used: JobFilter, sent_at: str) -> str:
    rows = ""
    for job in jobs:
        skills_text = ", ".join(job.skills[:5]) if job.skills else "N/A"
        rows += (
            f"<tr>"
            f'<td><a href="{job.url}">{job.title}</a></td>'
            f"<td>{job.company}</td>"
            f"<td>{job.location}</td>"
            f"<td>{_format_salary(job)}</td>"
            f"<td>{job.posted_at or 'N/A'}</td>"
            f"<td>{skills_text}</td>"
            f"<td>{job.source}</td>"
            f"</tr>\n"
        )

    req = ", ".join(filter_used.required_skills) or "none"
    return (
        "<!DOCTYPE html><html><head><style>"
        "table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #ddd;padding:8px;text-align:left}"
        "th{background:#4CAF50;color:white}"
        "tr:nth-child(even){background:#f2f2f2}"
        "a{color:#1a0dab}"
        "</style></head><body>"
        f"<h2>{len(jobs)} new job(s) matching your criteria</h2>"
        "<table><tr>"
        "<th>Title</th><th>Company</th><th>Location</th>"
        "<th>Salary</th><th>Posted</th><th>Skills</th><th>Source</th>"
        f"</tr>\n{rows}</table>"
        f'<p style="color:#888;font-size:12px;">'
        f"min_salary={filter_used.min_salary} | required_skills={req}<br>"
        f"Sent: {sent_at}</p>"
        "</body></html>"
    )


class EmailAlertSender(AlertSender):
    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        from_addr: str,
        to_addrs: list[str],
        password: str | None = None,
    ) -> None:
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._from_addr = from_addr
        self._to_addrs = to_addrs
        self._password = password

    def send(self, jobs: list[Job], filter_used: JobFilter) -> bool:
        if not jobs:
            return True
        if not self._to_addrs:
            logger.warning("No recipients configured for email alert")
            return False

        sent_at = datetime.now(UTC).isoformat()
        subject = f"{len(jobs)} new job(s) matching your criteria"
        html_body = build_html_body(jobs, filter_used, sent_at)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self._from_addr
        msg["To"] = ", ".join(self._to_addrs)
        msg.attach(MIMEText(html_body, "html"))

        try:
            with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=30) as server:
                server.ehlo()
                server.starttls()
                if self._password:
                    server.login(self._from_addr, self._password)
                server.sendmail(
                    self._from_addr, self._to_addrs, msg.as_string()
                )
            logger.info("Alert sent: %d jobs → %s", len(jobs), self._to_addrs)
            return True
        except (smtplib.SMTPException, OSError) as exc:
            logger.error("Failed to send email alert: %s", exc)
            return False
