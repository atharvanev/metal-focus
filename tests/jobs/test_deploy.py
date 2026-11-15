import os
import plistlib
import subprocess
import time
from pathlib import Path

from jobs import deploy as d


def test_agents_without_prompts_skip_nudges_and_checkins():
    a = {x.label: x for x in d.agents(prompts=False)}
    assert set(a) == {d.RUNNER, d.NIGHTLY}
    assert a[d.RUNNER].args == ("hsmm.runner", "run", "--no-nudge")


def test_agents_with_prompts():
    a = {x.label: x for x in d.agents(prompts=True)}
    assert set(a) == {d.RUNNER, d.NIGHTLY, d.CHECKINS}
    assert "--no-nudge" not in a[d.RUNNER].args
    assert a[d.CHECKINS].args == ("labels.checkins", "run")


def test_render_plist(tmp_path):
    py, cwd = tmp_path / "rt/current/.venv/bin/python", tmp_path / "rt/current"
    runner, nightly = d.agents(False)
    p = plistlib.loads(d.render_plist(runner, py, cwd, tmp_path, {"METAL_FOCUS_HOME": "/x"}))
    assert p["ProgramArguments"] == [str(py), "-m", "hsmm.runner", "run", "--no-nudge"]
    assert p["WorkingDirectory"] == str(cwd) and p["KeepAlive"] == {"SuccessfulExit": False} and p["RunAtLoad"]
    assert p["EnvironmentVariables"] == {"PYTHONUNBUFFERED": "1", "METAL_FOCUS_HOME": "/x"}
    assert p["StandardErrorPath"] == str(tmp_path / "hsmm-runner.err.log")
    n = plistlib.loads(d.render_plist(nightly, py, cwd, tmp_path))
    assert n["StartCalendarInterval"] == {"Hour": 3, "Minute": 30} and "KeepAlive" not in n


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    (repo / "a.py").write_text("committed\n")
    (repo / "logger").mkdir()
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c1")
    return repo


def test_export_tree_uses_committed_content_only(tmp_path):
    repo = _repo(tmp_path)
    (repo / "a.py").write_text("UNCOMMITTED\n")
    (repo / "scratch.txt").write_text("untracked\n")
    dest = tmp_path / "out"
    d.export_tree(repo, "main", dest)
    assert (dest / "a.py").read_text() == "committed\n"
    assert not (dest / "scratch.txt").exists() and not (dest / ".git").exists()


def test_copy_local_files(tmp_path):
    main, dest = tmp_path / "main", tmp_path / "dest"
    for p in (main / "logger", main / "labels", dest / "logger", dest / "labels"):
        p.mkdir(parents=True)
    (main / "logger/config.local.yaml").write_text("exclude: {}\n")
    assert d.copy_local_files(main, dest) == ["logger/config.local.yaml"]
    assert (dest / "logger/config.local.yaml").stat().st_mode & 0o777 == 0o600


def test_point_current_and_prune(tmp_path):
    rt = tmp_path / "rt"
    rt.mkdir()
    dirs = []
    for i in range(4):
        p = rt / f"sha{i}"
        p.mkdir()
        t = time.time() - 100 + i
        os.utime(p, (t, t))
        dirs.append(p)
    cur = d.point_current(rt, dirs[3])
    assert cur.is_symlink() and cur.resolve() == dirs[3].resolve()
    d.point_current(rt, dirs[2])
    assert cur.resolve() == dirs[2].resolve()
    d.prune(rt, dirs[2])
    left = sorted(p.name for p in rt.iterdir() if not p.is_symlink())
    assert "sha2" in left and len(left) == d.KEEP_RUNTIMES


def test_previous_prompts_kept(tmp_path):
    assert d.previous_prompts(tmp_path) is False
    (tmp_path / "DEPLOYED").write_text("abc\nmain\nprompts=1\n")
    assert d.previous_prompts(tmp_path) is True
    (tmp_path / "DEPLOYED").write_text("abc\nmain\nprompts=0\n")
    assert d.previous_prompts(tmp_path) is False


def test_cli_prompts_flags():
    import argparse
    from jobs.__main__ import main
    import jobs.__main__ as m
    seen = []
    orig = m.d.deploy
    m.d.deploy = lambda ref, prompts: seen.append(prompts)
    try:
        main(["deploy"]); main(["deploy", "--prompts"]); main(["deploy", "--no-prompts"])
    finally:
        m.d.deploy = orig
    assert seen == [None, True, False]
