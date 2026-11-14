from datetime import datetime

from bridge.store import Store
from tests.bridge.conftest import NOW, add_sample

CHECKIN_COLS = ["id", "asked_at", "trigger", "window_start", "window_end", "model_top_state",
                "model_top_prob", "response", "responded_at", "split"]


def test_schema_idempotent(conn):
    Store(conn)
    Store(conn)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(checkins)")]
    assert cols == CHECKIN_COLS


def test_status_running_and_counts(conn, store):
    add_sample(conn, NOW - 100)
    add_sample(conn, NOW - 5, excluded=1, app_name=None, window_title=None)
    s = store.status()
    assert s["logger_running"] is True
    assert s["samples_today"] == 2
    assert s["excluded_today"] == 1
    assert s["last_sample_ts"] == NOW - 5


def test_status_not_running_when_stale(conn, store):
    add_sample(conn, NOW - 13)
    assert store.status()["logger_running"] is False


def test_status_no_samples(store):
    s = store.status()
    assert s["last_sample_ts"] is None
    assert s["logger_running"] is False
    assert s["last_event"] is None


def test_status_last_event(conn, store):
    conn.execute("INSERT INTO events (ts, kind, detail) VALUES (?, 'wake', NULL)", (NOW - 3,))
    assert store.status()["last_event"] == {"ts": NOW - 3, "kind": "wake", "detail": None}


def test_samples_redact_excluded_and_private(conn, store):
    add_sample(conn, NOW - 30, excluded=1, app_name="Chase", window_title="Chase", domain="chase.com")
    add_sample(conn, NOW - 20, private=1, app_name="Google Chrome", window_title="secret", domain="x.com")
    add_sample(conn, NOW - 10)
    rows = store.samples(since=None, limit=200)
    assert [r["ts"] for r in rows] == [NOW - 10, NOW - 20, NOW - 30]
    for r in rows[1:]:
        assert r["window_title"] is None and r["domain"] is None and r["app_name"] is None
        assert r["bundle_id"] is None
    assert rows[2]["excluded"] == 1
    assert rows[0]["window_title"] == "x.py"
    assert len(store.samples(since=None, limit=1)) == 1
    assert [r["ts"] for r in store.samples(since=NOW - 20, limit=200)] == [NOW - 10]


def test_events_newest_first(conn, store):
    for i, k in enumerate(["sleep", "wake", "lock"]):
        conn.execute("INSERT INTO events (ts, kind) VALUES (?, ?)", (NOW - 10 + i, k))
    assert [e["kind"] for e in store.events(since=None, limit=2)] == ["lock", "wake"]


def test_timeline_from_samples(conn, store):
    for i in range(3):
        add_sample(conn, NOW - 100 + 4 * i)
    add_sample(conn, NOW - 88, app_name="Safari", idle_s=95.0)
    add_sample(conn, NOW - 84, excluded=1, app_name=None, window_title=None)
    day = datetime.fromtimestamp(NOW).strftime("%Y-%m-%d")
    segs = store.timeline(day)
    assert [s["label"] for s in segs] == ["Code", "Away", "Excluded"]
    assert all(s["source"] == "samples" for s in segs)
    assert segs[0]["start"] == NOW - 100 and segs[0]["end"] == NOW - 92 + 4.0


def test_timeline_other_day_empty(conn, store):
    add_sample(conn, NOW)
    assert store.timeline("2000-01-01") == []


def test_readout_missing_table_is_none(store):
    assert store.readout() is None


def _readouts(conn, written_at):
    conn.execute("CREATE TABLE readouts (window_start REAL, model TEXT, day TEXT, written_at REAL, "
                 "params TEXT, readout TEXT, PRIMARY KEY(window_start, model))")
    conn.execute("INSERT INTO readouts VALUES (?, 'hsmm', '2025-11-13', ?, '', ?)",
                 (NOW - 120, written_at, '{"risk": 0.7, "away": false}'))


def test_readout_fresh(conn, store):
    _readouts(conn, NOW - 10)
    r = store.readout()
    assert r["risk"] == 0.7 and r["window_start"] == NOW - 120 and r["written_at"] == NOW - 10


def test_readout_stale_is_none(conn, store):
    _readouts(conn, NOW - 301)
    assert store.readout() is None
