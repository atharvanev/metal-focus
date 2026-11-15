"""Deploy the Python background jobs as LaunchAgents, running from a pinned copy of the code.

    uv run python -m jobs deploy [--ref main] [--prompts | --no-prompts]   (default: keep the last setting)
    uv run python -m jobs status
    uv run python -m jobs uninstall

Why a copy: the repo lives under ~/Documents, which macOS protects; a launchd-started Python reading
it would need a Documents permission, and macOS permissions belong only to the signed app. The checkout
can also be on any branch. So `deploy` exports the committed tree of ``--ref`` (``git archive``, never
the working tree) into ``<data dir>/runtime/<sha>``, copies the gitignored personal configs, runs
``uv sync --frozen`` there, points ``runtime/current`` at it and (re)starts the agents. The jobs only
read code from that copy and only write to the data dir; they need no macOS permission.

Agents:
- hsmm-runner: steps the HSMM every new window, writes `readouts` (always on, KeepAlive).
- hsmm-nightly: nightly parameter update at 03:30.
- checkins (only with --prompts): the check-in scheduler. With --prompts the runner also runs the
  nudge engine. Leave prompts off until the menu bar app can show prompts (otherwise check-ins
  expire unanswered and count against the daily budget).
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from common.paths import data_dir

PREFIX = "com.metalfocus."
RUNNER, NIGHTLY, CHECKINS = PREFIX + "hsmm-runner", PREFIX + "hsmm-nightly", PREFIX + "checkins"
ALL_LABELS = (RUNNER, NIGHTLY, CHECKINS)
# Gitignored files that git archive can't carry: personal exclusions / category rules.
LOCAL_FILES = ("logger/config.local.yaml", "labels/categories.local.yaml")
KEEP_RUNTIMES = 2  # current + previous, for a quick rollback


@dataclass(frozen=True)
class Agent:
    label: str
    args: tuple[str, ...]
    keepalive: bool = False
    calendar: tuple[int, int] | None = None  # (hour, minute)


def agents(prompts: bool) -> list[Agent]:
    runner_args = ("hsmm.runner", "run") + (() if prompts else ("--no-nudge",))
    out = [Agent(RUNNER, runner_args, keepalive=True), Agent(NIGHTLY, ("hsmm.nightly",), calendar=(3, 30))]
    if prompts:
        out.append(Agent(CHECKINS, ("labels.checkins", "run"), keepalive=True))
    return out


def render_plist(a: Agent, python: Path, cwd: Path, logs: Path, env: dict[str, str] | None = None) -> bytes:
    name = a.label.removeprefix(PREFIX)
    d: dict = {
        "Label": a.label,
        "ProgramArguments": [str(python), "-m", *a.args],
        "WorkingDirectory": str(cwd),
        "StandardOutPath": str(logs / f"{name}.out.log"),
        "StandardErrorPath": str(logs / f"{name}.err.log"),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", **(env or {})},
        "ProcessType": "Background",
    }
    if a.keepalive:
        # The jobs wait/retry when the DB or key isn't ready; KeepAlive only covers crashes.
        d |= {"RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False}, "ThrottleInterval": 30}
    if a.calendar:
        d["StartCalendarInterval"] = {"Hour": a.calendar[0], "Minute": a.calendar[1]}  # runs at wake if asleep
    return plistlib.dumps(d)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def export_tree(repo: Path, ref: str, dest: Path) -> None:
    """Write the committed tree of ``ref`` to ``dest`` (no working-tree changes, no .git)."""
    with tempfile.TemporaryDirectory() as tmp:
        tar = Path(tmp) / "tree.tar"
        subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", "-o", str(tar), ref], check=True)
        dest.mkdir(parents=True)
        with tarfile.open(tar) as t:
            t.extractall(dest, filter="data")


def copy_local_files(main_checkout: Path, dest: Path) -> list[str]:
    copied = []
    for rel in LOCAL_FILES:
        src = main_checkout / rel
        if src.is_file():
            shutil.copy2(src, dest / rel)
            (dest / rel).chmod(0o600)
            copied.append(rel)
    return copied


def prune(runtime: Path, keep: Path) -> None:
    dirs = sorted((p for p in runtime.iterdir() if p.is_dir() and not p.is_symlink()), key=lambda p: p.stat().st_mtime)
    old = [p for p in dirs if p != keep][: max(0, len(dirs) - KEEP_RUNTIMES)]
    for p in old:
        shutil.rmtree(p)


def point_current(runtime: Path, target: Path) -> Path:
    cur = runtime / "current"
    tmp = runtime / ".current.tmp"
    tmp.unlink(missing_ok=True)
    tmp.symlink_to(target.name)
    os.replace(tmp, cur)  # atomic swap
    return cur


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _launchctl(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=check)


def agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def previous_prompts(runtime: Path) -> bool:
    """The --prompts setting of the last deploy (so a plain redeploy never silently turns prompts off)."""
    f = runtime / "DEPLOYED"
    return f.exists() and "prompts=1" in f.read_text().split()


def deploy(ref: str = "main", prompts: bool | None = None, repo: Path | None = None) -> Path:
    repo = repo or Path(_git(Path(__file__).resolve().parent, "rev-parse", "--show-toplevel"))
    main_checkout = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")).parent
    sha = _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    home = data_dir()
    runtime = home / "runtime"
    runtime.mkdir(mode=0o700, exist_ok=True)
    if prompts is None:
        prompts = previous_prompts(runtime)
    dest = runtime / sha[:12]
    if not dest.exists():
        export_tree(repo, sha, dest)
    copied = copy_local_files(main_checkout, dest)
    uv = shutil.which("uv") or os.environ.get("UV")
    if not uv:
        raise SystemExit("uv not found on PATH")
    subprocess.run([uv, "sync", "--frozen", "--no-dev", "--quiet"], cwd=dest, check=True,
                   env={k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"})
    cur = point_current(runtime, dest)
    python = cur / ".venv" / "bin" / "python"  # through `current`, so a redeploy needs no plist edit

    wanted = agents(prompts)
    adir = agents_dir()
    adir.mkdir(parents=True, exist_ok=True)
    for label in ALL_LABELS:  # stop everything first so no job runs old code mid-swap
        _launchctl("bootout", f"{_domain()}/{label}")
    for label in set(ALL_LABELS) - {a.label for a in wanted}:
        (adir / f"{label}.plist").unlink(missing_ok=True)
    for a in wanted:
        path = adir / f"{a.label}.plist"
        env = {"METAL_FOCUS_HOME": os.environ["METAL_FOCUS_HOME"]} if "METAL_FOCUS_HOME" in os.environ else None
        path.write_bytes(render_plist(a, python, cur, home, env))
        _launchctl("bootstrap", _domain(), str(path), check=True)
    (runtime / "DEPLOYED").write_text(f"{sha}\n{ref}\nprompts={int(prompts)}\n")
    prune(runtime, dest)
    print(f"deployed {ref} @ {sha[:12]} -> {dest}")
    print(f"local config copied: {', '.join(copied) or 'none'}")
    print(f"agents: {', '.join(a.label for a in wanted)}   logs: {home}/*.log")
    return dest


def status() -> int:
    home = data_dir()
    dep = home / "runtime" / "DEPLOYED"
    print(f"deployed: {dep.read_text().split() if dep.exists() else 'nothing'}")
    for label in ALL_LABELS:
        r = _launchctl("print", f"{_domain()}/{label}")
        if r.returncode != 0:
            print(f"{label}: not loaded")
            continue
        fields = {}
        for line in r.stdout.splitlines():
            k, _, v = line.strip().partition(" = ")
            if k in ("state", "pid", "last exit code", "runs"):
                fields[k] = v
        print(f"{label}: " + ", ".join(f"{k}={v}" for k, v in fields.items()))
    return 0


def uninstall() -> None:
    for label in ALL_LABELS:
        _launchctl("bootout", f"{_domain()}/{label}")
        (agents_dir() / f"{label}.plist").unlink(missing_ok=True)
    print("removed agents " + ", ".join(ALL_LABELS) + " (runtime copies kept under the data dir)")
