"""Shared pre-flight checks for install_service.py and uninstall_service.py."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def check_preflight(
    project_dir: Path,
    *,
    check_notifications: bool = True,
) -> bool:
    """Return True if safe to proceed, False to abort.

    Hard failures (missing config / resume) print to stderr and return False.
    A notification failure is a soft warning: the user is prompted [y/N].
    """
    config = project_dir / "config.yaml"
    if not config.exists():
        print(
            f"Error: {config} not found. "
            "Copy config.yaml from the repo to get started.",
            file=sys.stderr,
        )
        return False

    resume = project_dir / "data" / "resume.txt"
    if not resume.exists():
        print(
            f"Error: {resume} not found. "
            "Add your plain-text resume there before installing the service.",
            file=sys.stderr,
        )
        return False

    if not check_notifications:
        return True

    result = subprocess.run(
        [sys.executable, "-m", "localjobscout", "--check"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("Warning: desktop notifications may not work on this system.")
        if result.stdout:
            print(result.stdout.rstrip())
        answer = input("Continue installing the service anyway? [y/N] ").strip().lower()
        return answer == "y"

    return True
