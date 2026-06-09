from __future__ import annotations

import logging
import platform

from plyer import notification as _notify

from localjobscout.db import Job

logger = logging.getLogger(__name__)

_TITLE_MAX = 63


def notify_match(job: Job, score: float) -> None:
    suffix = f" ({score:.0%})"
    body = f"New match: {job.title}"
    if len(body) + len(suffix) > _TITLE_MAX:
        body = body[: _TITLE_MAX - len(suffix) - 1] + "…"
    title = body + suffix

    company = job.company or "Unknown"
    location = job.location or "Unknown"
    message = f"{company} — {location}\n{job.url[:80]}"
    message = message[:255]

    try:
        _notify.notify(
            title=title,
            message=message,
            app_name="LocalJobScout",
            timeout=10,
        )
    except Exception as exc:
        logger.warning(
            "Notification failed for %r: %s: %s",
            job.title,
            type(exc).__name__,
            exc,
        )


def check_notifications_available() -> tuple[bool, str]:
    system = platform.system()
    if system in ("Darwin", "Windows"):
        return True, ""
    if system == "Linux":
        try:
            import dbus  # noqa: F401
        except ImportError:
            return (
                False,
                "Linux notifications require dbus-python. Install with: "
                "pip install dbus-python (also requires system libdbus-1-dev)",
            )
        return True, ""
    return False, f"Notifications not supported on {system}"
