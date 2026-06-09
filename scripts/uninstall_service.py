"""Remove the LocalJobScout background service on macOS, Linux, or Windows."""
from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

from _preflight import check_preflight

_PLIST_LABEL = "com.localjobscout"
_LINUX_SERVICE = "localjobscout.service"
_WIN_TASK = "LocalJobScout"


def uninstall_macos() -> int:
    plist_path = (
        Path.home() / "Library" / "LaunchAgents" / f"{_PLIST_LABEL}.plist"
    )

    result = subprocess.run(
        ["launchctl", "unload", str(plist_path)],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        print(
            f"Note: launchctl unload returned {result.returncode} "
            "(service may not have been loaded)"
        )

    if plist_path.exists():
        plist_path.unlink()
        print(f"Removed: {plist_path}")
    else:
        print(f"Not found (already removed?): {plist_path}")

    print("LocalJobScout service uninstalled.")
    return 0


def uninstall_linux() -> int:
    subprocess.run(
        ["systemctl", "--user", "disable", "--now", _LINUX_SERVICE],
        capture_output=True,
        check=False,
    )

    unit_path = (
        Path.home() / ".config" / "systemd" / "user" / _LINUX_SERVICE
    )
    if unit_path.exists():
        unit_path.unlink()
        print(f"Removed: {unit_path}")
    else:
        print(f"Not found (already removed?): {unit_path}")

    subprocess.run(
        ["systemctl", "--user", "daemon-reload"],
        capture_output=True,
        check=False,
    )

    print("LocalJobScout service uninstalled.")
    return 0


_WIN_REG_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"


def uninstall_windows(project_dir: Path) -> int:
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _WIN_REG_KEY,
            access=winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, _WIN_TASK)
        print(f"Removed registry entry: {_WIN_TASK}")
    except FileNotFoundError:
        print(f"Registry entry not found (already removed?): {_WIN_TASK}")

    bat_path = project_dir / "scripts" / "run_service.bat"
    if bat_path.exists():
        bat_path.unlink()
        print(f"Removed: {bat_path}")

    print("LocalJobScout service uninstalled.")
    return 0


def main() -> int:
    system = platform.system()
    project_dir = Path(__file__).resolve().parent.parent

    if not check_preflight(project_dir, check_notifications=False):
        return 1

    if system == "Darwin":
        return uninstall_macos()
    if system == "Linux":
        return uninstall_linux()
    if system == "Windows":
        return uninstall_windows(project_dir)

    print(f"Unsupported platform: {system}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
