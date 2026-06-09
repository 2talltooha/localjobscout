"""Install LocalJobScout as a background service on macOS, Linux, or Windows."""
from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

from _preflight import check_preflight

_PLIST_LABEL = "com.localjobscout"
_LINUX_SERVICE = "localjobscout.service"
_WIN_TASK = "LocalJobScout"

# Split to keep Python source lines under 88 chars.
_PLIST_DOCTYPE = (
    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"'
    ' "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
)


def install_macos(project_dir: Path, python_exe: str) -> int:
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / f"{_PLIST_LABEL}.plist"

    data_dir = project_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / "service.log"
    err_path = data_dir / "service.err"

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        _PLIST_DOCTYPE,
        '<plist version="1.0">',
        "<dict>",
        "    <key>Label</key>",
        f"    <string>{_PLIST_LABEL}</string>",
        "    <key>ProgramArguments</key>",
        "    <array>",
        f"        <string>{python_exe}</string>",
        "        <string>-m</string>",
        "        <string>localjobscout</string>",
        "    </array>",
        "    <key>WorkingDirectory</key>",
        f"    <string>{project_dir}</string>",
        "    <key>RunAtLoad</key>",
        "    <true/>",
        "    <key>KeepAlive</key>",
        "    <true/>",
        "    <key>StandardOutPath</key>",
        f"    <string>{log_path}</string>",
        "    <key>StandardErrorPath</key>",
        f"    <string>{err_path}</string>",
        "</dict>",
        "</plist>",
        "",
    ]
    plist_path.write_text("\n".join(lines), encoding="utf-8")

    # Unload silently in case it was previously loaded.
    subprocess.run(
        ["launchctl", "unload", str(plist_path)],
        check=False,
        capture_output=True,
    )
    subprocess.run(["launchctl", "load", str(plist_path)], check=True)

    print(f"Service installed: {_PLIST_LABEL}")
    print(f"View logs:   tail -f {log_path}")
    print("Uninstall:   python scripts/uninstall_service.py")
    return 0


def install_linux(project_dir: Path, python_exe: str) -> int:
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / _LINUX_SERVICE

    data_dir = project_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / "service.log"
    err_path = data_dir / "service.err"

    unit_content = "\n".join(
        [
            "[Unit]",
            "Description=LocalJobScout — local job hunting daemon",
            "After=network.target",
            "",
            "[Service]",
            "Type=simple",
            f"WorkingDirectory={project_dir}",
            f"ExecStart={python_exe} -m localjobscout",
            "Restart=on-failure",
            "RestartSec=30",
            f"StandardOutput=append:{log_path}",
            f"StandardError=append:{err_path}",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )
    unit_path.write_text(unit_content, encoding="utf-8")

    reload = subprocess.run(
        ["systemctl", "--user", "daemon-reload"],
        capture_output=True,
        text=True,
    )
    if reload.returncode != 0:
        print(
            "Error: 'systemctl --user daemon-reload' failed.\n"
            "If you are running as root or without a persistent user session, run:\n"
            "    loginctl enable-linger $USER\n"
            "then try again.",
            file=sys.stderr,
        )
        return 1

    enable = subprocess.run(
        ["systemctl", "--user", "enable", "--now", _LINUX_SERVICE],
        capture_output=True,
        text=True,
    )
    if enable.returncode != 0:
        print(
            f"Error: failed to enable {_LINUX_SERVICE}.\n{enable.stderr}",
            file=sys.stderr,
        )
        return 1

    print(f"Service installed: {_LINUX_SERVICE} (user)")
    print("View logs:   journalctl --user -u localjobscout -f")
    print(f"         or  tail -f {log_path}")
    print("Uninstall:   python scripts/uninstall_service.py")
    return 0


_WIN_REG_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"


def install_windows(project_dir: Path, python_exe: str) -> int:
    import winreg

    scripts_dir = project_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    bat_path = scripts_dir / "run_service.bat"

    data_dir = project_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / "service.log"
    err_path = data_dir / "service.err"

    bat_lines = [
        "@echo off",
        f'cd /d "{project_dir}"',
        f'"{python_exe}" -m localjobscout >> "{log_path}" 2>> "{err_path}"',
        "",
    ]
    bat_path.write_text("\r\n".join(bat_lines), encoding="utf-8")

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        _WIN_REG_KEY,
        access=winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(key, _WIN_TASK, 0, winreg.REG_SZ, str(bat_path))

    print(f"Service installed: {_WIN_TASK} (HKCU Run key — starts on next login)")
    print(f'View logs:   type "{log_path}"')
    print("Uninstall:   python scripts/uninstall_service.py")
    return 0


def main() -> int:
    system = platform.system()
    project_dir = Path(__file__).resolve().parent.parent
    python_exe = sys.executable

    if not check_preflight(project_dir):
        return 1

    if system == "Darwin":
        return install_macos(project_dir, python_exe)
    if system == "Linux":
        return install_linux(project_dir, python_exe)
    if system == "Windows":
        return install_windows(project_dir, python_exe)

    print(f"Unsupported platform: {system}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
