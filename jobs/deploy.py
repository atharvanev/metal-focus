"""Deploy the Python background jobs for the menu bar app to run, from a pinned copy of the code.

    uv run python -m jobs deploy [--ref main] [--prompts | --no-prompts]   (default: keep the last setting)
    uv run python -m jobs status
    uv run python -m jobs uninstall

Why a copy: the repo lives under ~/Documents, which macOS protects; a Python job reading it would
need a Documents permission, and macOS permissions belong only to the signed app. The checkout can
also be on any branch. So `deploy` exports the committed tree of ``--ref`` (``git archive``, never
the working tree) into ``<data dir>/runtime/<sha>``, copies the gitignored personal configs, runs
``uv sync --frozen`` there, points ``runtime/current`` at it and writes ``runtime/jobs.json``.

Who runs them: the menu bar app (the only login item). It runs each job in ``jobs.json`` as a child
process (``runtime/current/.venv/bin/python -m ...``), restarts crashed keepalive jobs, runs daily
jobs once a day after their time, and restarts everything when ``jobs.json`` changes. Children exit
when the app does (``common.supervised``). There are no LaunchAgents: each one showed up as a
separate unsigned "python" background item. ``deploy`` removes the old ones.

Jobs:
- hsmm-runner: steps the HSMM every new window, writes `readouts` (always on).
- hsmm-nightly: nightly parameter update after 03:30 (once per day).
- checkins (only with --prompts): the check-in scheduler. With --prompts the runner also runs the
  nudge engine.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from common.paths import data_dir

# The LaunchAgents earlier deploys installed; deploy/uninstall remove them (the app runs the jobs now).
PREFIX = "com.metalfocus."
LEGACY_AGENTS = tuple(PREFIX + n for n in ("hsmm-runner", "hsmm-nightly", "checkins"))
RUNNER, NIGHTLY, CHECKINS = "hsmm-runner", "hsmm-nightly", "checkins"
# Gitignored files that git archive can't carry: personal exclusions / category rules.
LOCAL_FILES = ("logger/config.local.yaml", "labels/categories.local.yaml")
KEEP_RUNTIMES = 2  # current + previous, for a quick rollback
MANIFEST = "jobs.json"  # read by the app (JobManifest.swift)


@dataclass(frozen=True)
class Job:
    name: str
    args: tuple[str, ...]  # after `python -m`
    keepalive: bool = False
    daily: tuple[int, int] | None = None  # (hour, minute)


def jobs(prompts: bool) -> list[Job]:
    runner_args = ("hsmm.runner", "run") + (() if prompts else ("--no-nudge",))
    out = [Job(RUNNER, runner_args, keepalive=True), Job(NIGHTLY, ("hsmm.nightly",), daily=(3, 30))]
    if prompts:
        out.append(Job(CHECKINS, ("labels.checkins", "run"), keepalive=True))
    return out


def render_manifest(sha: str, js: list[Job]) -> str:
    return json.dumps({"sha": sha, "jobs": [
        {"name": j.name, "args": list(j.args), "keepalive": j.keepalive,
         **({"daily": {"hour": j.daily[0], "minute": j.daily[1]}} if j.daily else {})}
        for j in js]}, indent=2) + "\n"


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


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


def agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def remove_legacy_agents() -> list[str]:
    removed = []
    for label in LEGACY_AGENTS:
        subprocess.run(["launchctl", "bootout", f"{_domain()}/{label}"], capture_output=True)
        plist = agents_dir() / f"{label}.plist"
        if plist.exists():
            plist.unlink()
            removed.append(label)
    return removed


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
    removed = remove_legacy_agents()  # before the app sees jobs.json, so no job runs twice
    point_current(runtime, dest)
    wanted = jobs(prompts)
    (runtime / "DEPLOYED").write_text(f"{sha}\n{ref}\nprompts={int(prompts)}\n")
    write_atomic(runtime / MANIFEST, render_manifest(sha, wanted))  # last: the app restarts the jobs on change
    prune(runtime, dest)
    print(f"deployed {ref} @ {sha[:12]} -> {dest}")
    print(f"local config copied: {', '.join(copied) or 'none'}")
    if removed:
        print(f"removed old LaunchAgents: {', '.join(removed)}")
    print(f"jobs: {', '.join(j.name for j in wanted)} (run by the menu bar app)   logs: {home}/*.log")
    return dest


def running_jobs() -> dict[str, list[str]]:
    """job name -> pids of `python -m <module>` processes running from the runtime dir."""
    cur = str(data_dir() / "runtime" / "current")
    out: dict[str, list[str]] = {}
    ps = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True).stdout
    for line in ps.splitlines():
        pid, ppid, cmd = (line.split(None, 2) + ["", ""])[:3]
        if cur in cmd and " -m " in cmd:
            out.setdefault(cmd.split(" -m ", 1)[1].split()[0], []).append(f"{pid} (parent {ppid})")
    return out


def status() -> int:
    runtime = data_dir() / "runtime"
    dep, man = runtime / "DEPLOYED", runtime / MANIFEST
    print(f"deployed: {dep.read_text().split() if dep.exists() else 'nothing'}")
    running = running_jobs()
    for j in json.loads(man.read_text())["jobs"] if man.exists() else []:
        pids = running.get(j["args"][0])
        print(f"{j['name']}: " + (f"running pid {', '.join(pids)}" if pids else "not running"))
    left = [p.name for p in (agents_dir() / f"{l}.plist" for l in LEGACY_AGENTS) if p.exists()]
    if left:
        print(f"old LaunchAgents still installed (the app won't run jobs until removed): {', '.join(left)}")
    return 0


def uninstall() -> None:
    (data_dir() / "runtime" / MANIFEST).unlink(missing_ok=True)
    removed = remove_legacy_agents()
    print("removed jobs.json (the app stops the jobs)" + (f"; removed {', '.join(removed)}" if removed else ""))
