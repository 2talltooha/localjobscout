"""Tests for the live closed-posting check used by --manual-queue --open."""

from __future__ import annotations

import httpx
import pytest

from localjobscout.__main__ import _verify_job_open


class _Resp:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


def _patch_get(monkeypatch: pytest.MonkeyPatch, resp: object | Exception) -> None:
    def fake_get(url: str, **kwargs: object) -> object:
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr(httpx, "get", fake_get)


def test_verify_open_for_live_page(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, _Resp(200, "Apply now — we are hiring!"))
    assert _verify_job_open("https://x/job/1") is True


def test_verify_closed_for_closed_banner(monkeypatch: pytest.MonkeyPatch) -> None:
    banner = (
        "This posting has been closed and applications are no longer "
        "being accepted."
    )
    _patch_get(monkeypatch, _Resp(200, banner))
    assert _verify_job_open("https://x/job/1") is False


def test_verify_closed_for_404(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, _Resp(404, "Not Found"))
    assert _verify_job_open("https://x/job/1") is False


def test_verify_closed_for_410(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, _Resp(410, "Gone"))
    assert _verify_job_open("https://x/job/1") is False


def test_verify_unknown_on_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # 5xx → can't tell; treat as open (None) rather than hide a good job.
    _patch_get(monkeypatch, _Resp(503, "Service Unavailable"))
    assert _verify_job_open("https://x/job/1") is None


def test_verify_unknown_on_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, httpx.ConnectError("boom"))
    assert _verify_job_open("https://x/job/1") is None
