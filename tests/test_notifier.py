from __future__ import annotations

import platform
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from localjobscout.db import Job, make_job_id
from localjobscout.notifier import check_notifications_available, notify_match

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MOCK_PATH = "localjobscout.notifier._notify.notify"


def _make_job(**kwargs: Any) -> Job:
    defaults: dict[str, Any] = dict(
        id=make_job_id("test", "https://example.com/job"),
        source="test",
        title="Software Developer",
        url="https://example.com/job",
        description="",
        company="Acme Corp",
        location="Waterloo, ON",
    )
    defaults.update(kwargs)
    return Job(**defaults)


# ---------------------------------------------------------------------------
# notify_match tests
# ---------------------------------------------------------------------------


def test_notify_match_calls_plyer(mocker: MockerFixture) -> None:
    """notify_match passes correctly formatted title and message to plyer."""
    mock_notify = mocker.patch(_MOCK_PATH)
    job = _make_job()
    notify_match(job, 0.75)

    mock_notify.assert_called_once()
    _, kwargs = mock_notify.call_args
    assert kwargs["title"] == "New match: Software Developer (75%)"
    assert "Acme Corp" in kwargs["message"]
    assert "Waterloo, ON" in kwargs["message"]
    assert "https://example.com/job" in kwargs["message"]
    assert kwargs["app_name"] == "LocalJobScout"
    assert kwargs["timeout"] == 10


def test_notify_match_swallows_exceptions(
    mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    """plyer raising any exception must not propagate; a warning is logged."""
    mocker.patch(_MOCK_PATH, side_effect=RuntimeError("dbus broken"))
    job = _make_job()

    with caplog.at_level("WARNING", logger="localjobscout.notifier"):
        result = notify_match(job, 0.5)

    assert result is None
    assert any("dbus broken" in r.message for r in caplog.records)


def test_notify_match_missing_company_location(mocker: MockerFixture) -> None:
    """Empty company/location strings render as 'Unknown' in the message."""
    mock_notify = mocker.patch(_MOCK_PATH)
    job = _make_job(company="", location="")
    notify_match(job, 0.6)

    _, kwargs = mock_notify.call_args
    assert "Unknown" in kwargs["message"]
    # Both slots should use the fallback
    assert kwargs["message"].startswith("Unknown — Unknown")


def test_notify_match_truncates_long_url(mocker: MockerFixture) -> None:
    """URLs longer than 80 chars are truncated to 80 chars in the message."""
    mock_notify = mocker.patch(_MOCK_PATH)
    long_url = "https://www.jobbank.gc.ca/en/job/" + "x" * 300
    job = _make_job(url=long_url)
    notify_match(job, 0.9)

    _, kwargs = mock_notify.call_args
    # The URL line in the message must be at most 80 characters
    url_line = kwargs["message"].split("\n", 1)[1]
    assert len(url_line) <= 80


def test_notify_match_title_capped_at_63_chars(mocker: MockerFixture) -> None:
    """Title must never exceed 63 chars — Windows szTip cap is 64 WCHAR."""
    mock_notify = mocker.patch(_MOCK_PATH)
    job = _make_job(title="A" * 500)
    notify_match(job, 0.85)

    _, kwargs = mock_notify.call_args
    assert len(kwargs["title"]) <= 63


def test_notify_match_message_capped_at_255_chars(mocker: MockerFixture) -> None:
    """Message must never exceed 255 chars — Windows szInfo cap is 256 WCHAR."""
    mock_notify = mocker.patch(_MOCK_PATH)
    job = _make_job(
        company="C" * 500,
        location="L" * 500,
        url="https://example.com/" + "u" * 500,
    )
    notify_match(job, 0.5)

    _, kwargs = mock_notify.call_args
    assert len(kwargs["message"]) <= 255


# ---------------------------------------------------------------------------
# check_notifications_available tests
# ---------------------------------------------------------------------------


def test_check_macos_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """macOS and Windows always return (True, "")."""
    for system_name in ("Darwin", "Windows"):
        monkeypatch.setattr(platform, "system", lambda s=system_name: s)
        available, hint = check_notifications_available()
        assert available is True
        assert hint == ""


def test_check_linux_dbus(monkeypatch: pytest.MonkeyPatch) -> None:
    """Linux: (False, hint) without dbus; (True, "") with dbus importable."""
    monkeypatch.setattr(platform, "system", lambda: "Linux")

    # Path 1: dbus not installed → hint mentions dbus-python
    monkeypatch.setitem(sys.modules, "dbus", None)
    available, hint = check_notifications_available()
    assert available is False
    assert "dbus-python" in hint

    # Path 2: dbus importable → available
    monkeypatch.setitem(sys.modules, "dbus", MagicMock())
    available, hint = check_notifications_available()
    assert available is True
    assert hint == ""


def test_check_unknown_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unrecognised platform → (False, message mentioning platform name)."""
    monkeypatch.setattr(platform, "system", lambda: "Plan9")
    available, hint = check_notifications_available()
    assert available is False
    assert "Plan9" in hint
