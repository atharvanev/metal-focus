"""LaunchAgent so the logger runs whenever you're logged in.

ProgramArguments uses the repo's ``.venv/bin/python`` *without* resolving the symlink: the resolved
uv interpreter would not see the venv's packages. macOS still attributes permissions
(Accessibility, Automation) to the real uv-managed interpreter binary behind it.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

from common.paths import data_dir

LABEL = "com.metalfocus.logger"
REPO = Path(__file__).resolve().parents[1]
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def venv_python(repo: Path = REPO) -> Path:
    return repo / ".venv" / "bin" / "python"


def render_plist(python: Path, repo: Path, log_dir: Path) -> bytes:
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [str(python), "-m", "logger", "run"],
        "WorkingDirectory": str(repo),
        "RunAtLoad": True,
        # Restart after any non-zero exit (crash, Keychain locked at login, exit 2 = key missing),
        # throttled to every 30 s; a clean stop (exit 0, e.g. SIGTERM) is not restarted.
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,
        "StandardOutPath": str(log_dir / "logger.out.log"),
        "StandardErrorPath": str(log_dir / "logger.err.log"),
    })


def _domain() -> str:
    return f"gui/{os.getuid()}"


def install() -> None:
    py = venv_python()
    if not py.exists():
        raise SystemExit(f"{py} not found; run `uv sync` first")
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_bytes(render_plist(py, REPO, data_dir()))
    subprocess.run(["launchctl", "bootout", f"{_domain()}/{LABEL}"], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", _domain(), str(PLIST_PATH)], check=True)
    print(f"installed {PLIST_PATH} (logs in {data_dir()})")


def uninstall() -> None:
    subprocess.run(["launchctl", "bootout", f"{_domain()}/{LABEL}"], capture_output=True)
    PLIST_PATH.unlink(missing_ok=True)
    print(f"removed {PLIST_PATH}")
