"""LaunchAgents for the HSMM live runner (always on) and the nightly update (03:30).

Mirrors logger/launchd.py: ProgramArguments uses the repo's .venv/bin/python
without resolving the symlink. Installing from a git worktree is refused,
because the plist would point at a directory that can be deleted; install
from the main checkout.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

RUNNER_LABEL = "com.metalfocus.hsmm-runner"
NIGHTLY_LABEL = "com.metalfocus.hsmm-nightly"
REPO = Path(__file__).resolve().parents[1]
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"


def venv_python(repo: Path = REPO) -> Path:
    return repo / ".venv" / "bin" / "python"


def is_worktree(repo: Path = REPO) -> bool:
    parts = repo.parts
    return any(parts[i : i + 2] == (".claude", "worktrees") for i in range(len(parts) - 1)) or (repo / ".git").is_file()


def render_runner_plist(python: Path, repo: Path, log_dir: Path) -> bytes:
    return plistlib.dumps(
        {
            "Label": RUNNER_LABEL,
            "ProgramArguments": [str(python), "-m", "hsmm.runner", "run"],
            "WorkingDirectory": str(repo),
            "RunAtLoad": True,
            # The runner itself waits/retries when the DB, key or windowing isn't ready;
            # KeepAlive only covers real crashes. Clean exit (SIGTERM) is not restarted.
            "KeepAlive": {"SuccessfulExit": False},
            "ThrottleInterval": 30,
            "StandardOutPath": str(log_dir / "hsmm-runner.out.log"),
            "StandardErrorPath": str(log_dir / "hsmm-runner.err.log"),
        }
    )


def render_nightly_plist(python: Path, repo: Path, log_dir: Path, hour: int = 3, minute: int = 30) -> bytes:
    return plistlib.dumps(
        {
            "Label": NIGHTLY_LABEL,
            "ProgramArguments": [str(python), "-m", "hsmm.nightly"],
            "WorkingDirectory": str(repo),
            "StartCalendarInterval": {"Hour": hour, "Minute": minute},  # runs at wake if asleep then
            "StandardOutPath": str(log_dir / "hsmm-nightly.out.log"),
            "StandardErrorPath": str(log_dir / "hsmm-nightly.err.log"),
        }
    )


def _domain() -> str:
    return f"gui/{os.getuid()}"


def install() -> None:
    from common.paths import data_dir

    if is_worktree():
        raise SystemExit(f"refusing to install from a git worktree ({REPO}); run this from the main checkout")
    py = venv_python()
    if not py.exists():
        raise SystemExit(f"{py} not found; run `uv sync` first")
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    logs = data_dir()
    for label, data in (
        (RUNNER_LABEL, render_runner_plist(py, REPO, logs)),
        (NIGHTLY_LABEL, render_nightly_plist(py, REPO, logs)),
    ):
        path = AGENTS_DIR / f"{label}.plist"
        path.write_bytes(data)
        subprocess.run(["launchctl", "bootout", f"{_domain()}/{label}"], capture_output=True)
        subprocess.run(["launchctl", "bootstrap", _domain(), str(path)], check=True)
        print(f"installed {path}")
    print(f"logs in {logs}")


def uninstall() -> None:
    for label in (RUNNER_LABEL, NIGHTLY_LABEL):
        subprocess.run(["launchctl", "bootout", f"{_domain()}/{label}"], capture_output=True)
        (AGENTS_DIR / f"{label}.plist").unlink(missing_ok=True)
        print(f"removed {AGENTS_DIR / f'{label}.plist'}")
