from __future__ import annotations

import platform
from pathlib import Path
from unittest.mock import MagicMock

import install_service
import pytest
from _preflight import check_preflight
from pytest_mock import MockerFixture

# ---------------------------------------------------------------------------
# Test 1: OS dispatch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "system,func_attr",
    [
        ("Darwin", "install_macos"),
        ("Linux", "install_linux"),
        ("Windows", "install_windows"),
    ],
)
def test_os_dispatch(
    system: str,
    func_attr: str,
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    monkeypatch.setattr(platform, "system", lambda: system)
    monkeypatch.setattr(install_service, "check_preflight", lambda _: True)
    mock_fn = mocker.patch.object(install_service, func_attr, return_value=0)

    result = install_service.main()

    assert result == 0
    mock_fn.assert_called_once()


def test_os_dispatch_unsupported_platform(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Plan9")
    monkeypatch.setattr(install_service, "check_preflight", lambda _: True)

    result = install_service.main()

    assert result == 1
    assert "Plan9" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Test 2: macOS plist contents
# ---------------------------------------------------------------------------


def test_plist_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    python_exe = "/usr/local/bin/python3"
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    mock_run = mocker.patch("subprocess.run", return_value=MagicMock(returncode=0))

    result = install_service.install_macos(project_dir, python_exe)

    assert result == 0
    plist_path = (
        tmp_path / "Library" / "LaunchAgents" / "com.localjobscout.plist"
    )
    assert plist_path.exists()
    content = plist_path.read_text()
    assert python_exe in content
    assert str(project_dir) in content

    # launchctl load must have been called with the plist path
    all_cmds = [c.args[0] for c in mock_run.call_args_list]
    assert any(
        "launchctl" in cmd and "load" in cmd and str(plist_path) in cmd
        for cmd in all_cmds
    )


# ---------------------------------------------------------------------------
# Test 3: Linux systemd unit contents
# ---------------------------------------------------------------------------


def test_systemd_unit_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    python_exe = "/usr/bin/python3"
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    mocker.patch("subprocess.run", return_value=MagicMock(returncode=0))

    result = install_service.install_linux(project_dir, python_exe)

    assert result == 0
    unit_path = (
        tmp_path / ".config" / "systemd" / "user" / "localjobscout.service"
    )
    assert unit_path.exists()
    content = unit_path.read_text()
    assert python_exe in content
    assert str(project_dir) in content


# ---------------------------------------------------------------------------
# Test 4: Windows bat file and schtasks call
# ---------------------------------------------------------------------------


def test_windows_bat_file(
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    python_exe = r"C:\Python311\python.exe"
    project_dir = tmp_path / "project"
    (project_dir / "scripts").mkdir(parents=True)

    mock_set_value = mocker.patch("winreg.SetValueEx")
    mocker.patch("winreg.OpenKey")

    result = install_service.install_windows(project_dir, python_exe)

    assert result == 0
    bat_path = project_dir / "scripts" / "run_service.bat"
    assert bat_path.exists()
    content = bat_path.read_text()
    assert python_exe in content
    assert str(project_dir) in content

    mock_set_value.assert_called_once()
    _, value_name, _, _, value_data = mock_set_value.call_args.args
    assert value_name == "LocalJobScout"
    assert str(bat_path) in value_data


# ---------------------------------------------------------------------------
# Test 5: Preflight bails on missing resume
# ---------------------------------------------------------------------------


def test_preflight_missing_resume(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("location: Waterloo, ON\n")
    # data/resume.txt intentionally NOT created

    result = check_preflight(tmp_path)

    assert result is False


def test_preflight_missing_config(tmp_path: Path) -> None:
    # No config.yaml either

    result = check_preflight(tmp_path)

    assert result is False
