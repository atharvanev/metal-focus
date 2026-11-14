import json
import sqlite3
from dataclasses import dataclass

import pytest

from hsmm.runner import LiveRunner, latest_readout


@dataclass(frozen=True)
class W:  # mimics data.windows.Window
    window_start: float
    category: str
    switch_count: int
    is_away: int = 0


def _day(n, t0=1_000_000.0, away_at=()):
    cats = ["Coding", "Coding", "Docs", "Video", "Social", "Video"]
    return [W(t0 + 120 * i, cats[i % len(cats)], i % 6, int(i in away_at)) for i in range(n)]


class FakeStore:
    def __init__(self):
        self.days: dict[str, list[W]] = {}

    def load_day(self, conn, day):
        return list(self.days.get(day, []))


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    yield c
    c.close()


def test_writes_one_row_per_new_window_without_duplicates(conn):
    store = FakeStore()
    r = LiveRunner(conn, store.load_day)
    store.days["2026-11-13"] = _day(5)
    assert len(r.tick("2026-11-13")) == 5
    assert r.tick("2026-11-13") == []  # nothing new
    store.days["2026-11-13"] = _day(8)
    assert len(r.tick("2026-11-13")) == 3
    assert conn.execute("SELECT COUNT(*) FROM readouts").fetchone()[0] == 8
    latest = latest_readout(conn)
    assert latest["window_start"] == _day(8)[-1].window_start
    assert 0.0 <= latest["risk"] <= 1.0 and len(latest["p_state"]) == 4


def test_restart_replays_day_to_same_belief(conn):
    store = FakeStore()
    store.days["d"] = _day(10, away_at={4})
    a = LiveRunner(conn, store.load_day)
    a.tick("d")
    store.days["d"] = _day(14, away_at={4})
    continuous = json.loads(a.tick("d")[-1]["readout"])

    fresh_conn = sqlite3.connect(":memory:")
    b = LiveRunner(fresh_conn, store.load_day)  # "process restarted" mid-day
    replayed = json.loads(b.tick("d")[-1]["readout"])
    assert replayed["p_state"] == pytest.approx(continuous["p_state"], abs=1e-6)


def test_new_day_resets_model(conn):
    store = FakeStore()
    store.days["d1"] = _day(12)
    store.days["d2"] = _day(1, t0=2_000_000.0)
    r = LiveRunner(conn, store.load_day)
    r.tick("d1")
    first_d2 = json.loads(r.tick("d2")[0]["readout"])
    solo = json.loads(LiveRunner(sqlite3.connect(":memory:"), store.load_day).tick("d2")[0]["readout"])
    assert first_d2["p_state"] == pytest.approx(solo["p_state"], abs=1e-6)


def test_away_rows_are_flagged(conn):
    store = FakeStore()
    store.days["d"] = _day(6, away_at={5})
    rows = LiveRunner(conn, store.load_day).tick("d")
    assert json.loads(rows[-1]["readout"])["away"] is True
    assert json.loads(rows[-2]["readout"])["away"] is False


def test_rebuilt_day_with_same_length_is_replayed_and_rows_rewritten(conn):
    store = FakeStore()
    store.days["d"] = _day(6)
    r = LiveRunner(conn, store.load_day)
    r.tick("d")
    # categorizer rule change: window 2 recategorized, same number of windows
    rebuilt = _day(6)
    rebuilt[2] = W(rebuilt[2].window_start, "Video", 0)
    store.days["d"] = rebuilt
    rows = r.tick("d")
    assert len(rows) == 6  # whole day replayed
    fresh = LiveRunner(sqlite3.connect(":memory:"), store.load_day).tick("d")
    assert rows[-1]["readout"] == fresh[-1]["readout"]
    assert conn.execute("SELECT COUNT(*) FROM readouts").fetchone()[0] == 6
    stored = latest_readout(conn)
    assert stored["p_state"] == pytest.approx(json.loads(fresh[-1]["readout"])["p_state"])


class _FakeDB:
    class KeyMissingError(Exception):
        pass

    class WrongKeyError(Exception):
        pass

    def __init__(self, failures):
        self.failures = list(failures)

    def open_existing(self, db_path=None, store=None):
        if self.failures:
            raise self.failures.pop(0)
        return sqlite3.connect(":memory:")


def test_main_retries_on_cant_run_errors(monkeypatch):
    import sys
    import types

    import hsmm.runner as runner_mod

    fake = _FakeDB([])
    fake.failures = [FileNotFoundError("no db"), fake.KeyMissingError("no key"), fake.WrongKeyError("bad key")]
    data_pkg = types.ModuleType("data")
    db_mod = types.ModuleType("data.db")
    db_mod.open_existing = fake.open_existing
    db_mod.KeyMissingError, db_mod.WrongKeyError = fake.KeyMissingError, fake.WrongKeyError
    win_mod = types.ModuleType("data.windows")
    win_mod.load_day = lambda conn, day: _day(3)
    monkeypatch.setitem(sys.modules, "data", data_pkg)
    monkeypatch.setitem(sys.modules, "data.db", db_mod)
    monkeypatch.setitem(sys.modules, "data.windows", win_mod)

    ticks = {"n": 0}

    class Stop(Exception):
        pass

    def fake_sleep(_):
        ticks["n"] += 1
        if ticks["n"] >= 5:
            raise Stop

    monkeypatch.setattr(runner_mod.time, "sleep", fake_sleep)
    with pytest.raises(Stop):
        runner_mod.main(["--interval", "0"])
    assert fake.failures == []  # survived all three errors, then connected and ran


def test_main_reconnects_on_sqlcipher_error(monkeypatch):
    """sqlcipher3 errors don't subclass sqlite3.Error; the reconnect path must still run."""
    import sys
    import types

    import sqlcipher3

    import hsmm.runner as runner_mod

    opens = {"n": 0}

    def open_existing(db_path=None, store=None):
        opens["n"] += 1
        return sqlite3.connect(":memory:")

    calls = {"n": 0}

    def load_day(conn, day):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlcipher3.OperationalError("database is locked")
        return _day(2)

    db_mod = types.ModuleType("data.db")
    db_mod.open_existing = open_existing
    win_mod = types.ModuleType("data.windows")
    win_mod.load_day = load_day
    monkeypatch.setitem(sys.modules, "data", types.ModuleType("data"))
    monkeypatch.setitem(sys.modules, "data.db", db_mod)
    monkeypatch.setitem(sys.modules, "data.windows", win_mod)

    class Stop(Exception):
        pass

    ticks = {"n": 0}

    def fake_sleep(_):
        ticks["n"] += 1
        if ticks["n"] >= 3:
            raise Stop

    monkeypatch.setattr(runner_mod.time, "sleep", fake_sleep)
    with pytest.raises(Stop):
        runner_mod.main(["--interval", "0"])
    assert opens["n"] == 2  # reopened after the locked error
    assert calls["n"] >= 2  # and kept ticking
