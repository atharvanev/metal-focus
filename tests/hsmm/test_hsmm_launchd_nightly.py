import json
import plistlib
import sys
import types
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import mlx.core as mx
import pytest

from hsmm import launchd, nightly
from hsmm.params import HSMMParams
from hsmm.simulate import default_true_params, sample_days


def test_plists_render():
    py, repo, logs = Path("/r/.venv/bin/python"), Path("/r"), Path("/logs")
    run = plistlib.loads(launchd.render_runner_plist(py, repo, logs))
    assert run["Label"] == "com.metalfocus.hsmm-runner"
    assert run["ProgramArguments"] == ["/r/.venv/bin/python", "-m", "hsmm.runner", "run"]
    assert run["KeepAlive"] == {"SuccessfulExit": False} and run["WorkingDirectory"] == "/r"
    nt = plistlib.loads(launchd.render_nightly_plist(py, repo, logs))
    assert nt["ProgramArguments"][-1] == "hsmm.nightly"
    assert nt["StartCalendarInterval"] == {"Hour": 3, "Minute": 30}
    assert "KeepAlive" not in nt


def test_install_refused_from_worktree(tmp_path):
    wt = tmp_path / "repo" / ".claude" / "worktrees" / "x"
    wt.mkdir(parents=True)
    assert launchd.is_worktree(wt)
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    assert not launchd.is_worktree(main)
    # this test suite itself runs in a worktree: install must refuse before touching launchctl
    if launchd.is_worktree():
        with pytest.raises(SystemExit, match="worktree"):
            launchd.install()


# ---------------------------------------------------------------- nightly


@dataclass(frozen=True)
class W:
    window_start: float
    category: str
    switch_count: int
    is_away: int = 0


CATS = ["Coding", "Docs", "Chat", "Video", "Social"]
BIN_COUNT = [0, 3, 6]


def _windows(day_obs, t0):
    return [
        W(t0 + 120 * i, CATS[c] if c >= 0 else "Other", BIN_COUNT[s] if s >= 0 else 0, int(a))
        for i, (c, s, a) in enumerate(zip(day_obs.cat, day_obs.sw, day_obs.away or [False] * len(day_obs)))
    ]


@pytest.fixture
def fake_data(monkeypatch, tmp_path):
    monkeypatch.setenv("METAL_FOCUS_HOME", str(tmp_path))
    sims = sample_days(default_true_params(), 7, 120, seed=11)  # 2026-11-07 .. 2026-11-13
    by_day = {f"2026-11-{7 + i:02d}": _windows(d, 1e6 + 86400 * i) for i, d in enumerate(sims)}
    win_mod = types.ModuleType("data.windows")
    win_mod.load_day = lambda conn, day: by_day.get(day, [])
    merge_mod = types.ModuleType("labels.merge")
    merge_mod.load_training_labels = lambda conn, day: [(None, None)] * len(by_day.get(day, []))
    monkeypatch.setitem(sys.modules, "data.windows", win_mod)
    monkeypatch.setitem(sys.modules, "labels.merge", merge_mod)
    return by_day


def test_nightly_trains_on_completed_days_and_saves_atomically(fake_data, tmp_path):
    today = date(2026, 11, 13)  # 2026-11-13 has data but must NOT be used (today)
    out = tmp_path / "hsmm_params.json"
    s = nightly.run_nightly(None, today, days_back=28, min_days=3, out=out)
    assert s["status"] == "saved"
    assert "2026-11-13" in fake_data  # today has data...
    assert "2026-11-13" not in s["days"] and len(s["days"]) == 6  # ...but is never trained on
    p = HSMMParams.load(out)
    assert nightly.is_finite(p)
    assert (tmp_path / "hsmm_params_history" / "2026-11-13.json").exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_nightly_skips_with_too_few_days(fake_data, tmp_path):
    s = nightly.run_nightly(None, date(2026, 11, 9), days_back=28, min_days=3, out=tmp_path / "p.json")
    assert s["status"] == "skipped" and not (tmp_path / "p.json").exists()


def test_nightly_never_saves_non_finite(fake_data, tmp_path, monkeypatch):
    import hsmm.train

    bad = default_true_params()
    bad = HSMMParams(pi=bad.pi, A=bad.A, B=bad.B * mx.nan, C=bad.C, mu=bad.mu, r=bad.r, D=bad.D)
    monkeypatch.setattr(hsmm.train, "nightly_update", lambda p, b, c: (bad, type("C", (), {"loglik": 0.0, "occ": mx.zeros(4)})()))
    s = nightly.run_nightly(None, date(2026, 11, 13), out=tmp_path / "p.json")
    assert s["status"] == "error" and not (tmp_path / "p.json").exists()


def test_save_atomic_roundtrip(tmp_path):
    p = default_true_params()
    nightly.save_atomic(p, tmp_path / "x.json")
    assert json.loads((tmp_path / "x.json").read_text())["D"] == 30
