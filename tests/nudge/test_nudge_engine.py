import json
import sqlite3
from dataclasses import dataclass

import pytest

from hsmm.runner import LiveRunner
from nudge.engine import is_holdout, on_tick
from nudge.rule import Point, load_config, replay, should_fire
from nudge.text import build_body, hops, session_start_index

# Exact DDL from metal-focus-87 (bridge/schema.sql). Test fixture only: nudge/ never creates these.
BRIDGE_DDL = """
CREATE TABLE IF NOT EXISTS nudge_responses (
  id INTEGER PRIMARY KEY, nudge_id TEXT, asked_at REAL NOT NULL, responded_at REAL,
  response TEXT CHECK (response IS NULL OR response IN ('back_to_work','on_break','snooze','dismissed')),
  response_latency_s REAL, window_start REAL, window_end REAL);
CREATE TABLE IF NOT EXISTS notify_outbox (
  id INTEGER PRIMARY KEY, created_at REAL NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('checkin','nudge')),
  ref_id INTEGER, payload_json TEXT NOT NULL DEFAULT '{}',
  delivered_at REAL, expires_at REAL NOT NULL);
"""
SAMPLES_DDL = """
CREATE TABLE samples (id INTEGER PRIMARY KEY, ts REAL NOT NULL, app_name TEXT, domain TEXT,
  private INTEGER, excluded INTEGER NOT NULL, locked INTEGER NOT NULL);
"""

CFG = {**load_config(), "holdout_frac": 0.0}
T0 = 1_000_000.0


@dataclass(frozen=True)
class W:
    window_start: float
    category: str
    switch_count: int
    is_away: int = 0


def P(i, risk, away=False):
    return Point(T0 + 120 * i, risk, away)


# ---------------------------------------------------------------- rule


def test_rule_needs_two_consecutive_non_away_above_threshold():
    assert not should_fire([P(0, 0.9)], None, CFG)
    assert should_fire([P(0, 0.9), P(1, 0.7)], None, CFG)
    assert not should_fire([P(0, 0.9), P(1, 0.6)], None, CFG)  # strictly greater
    assert should_fire([P(0, 0.9), P(1, 0.1, away=True), P(2, 0.8)], None, CFG)  # away skipped
    assert not should_fire([P(0, 0.9), P(1, 0.9, away=True)], None, CFG)  # never on an away window


def test_rule_cooldown_ten_windows():
    pts = [P(i, 0.9) for i in range(25)]
    assert replay(pts, CFG) == [1, 11, 21]


# ---------------------------------------------------------------- text


WORK, DIST = ("Coding", "Docs"), ("Chat", "Video", "Social")


def test_session_start_allows_one_window_work_blip():
    ws = [W(0, "Coding", 0), W(1, "Coding", 0), W(2, "Video", 0), W(3, "Docs", 0), W(4, "Social", 0), W(5, "Video", 0)]
    assert session_start_index(ws, WORK, DIST) == 2
    ws2 = ws[:2] + [W(2, "Video", 0), W(3, "Docs", 0), W(4, "Docs", 0), W(5, "Video", 0)]
    assert session_start_index(ws2, WORK, DIST) == 5


@pytest.mark.parametrize("boundary", ["Excluded", "Other"])
def test_excluded_and_other_end_the_session(boundary):
    """An hour of confidential (Excluded) work before YouTube must not become part of the session."""
    ws = [W(0, "Coding", 0)] + [W(1 + i, boundary, 0) for i in range(10)] + [W(11 + i, "Video", 0) for i in range(3)]
    assert session_start_index(ws, WORK, DIST) == 11


def test_away_ends_the_session():
    ws = [W(0, "Video", 0), W(1, "Video", 0, is_away=1), W(2, "Social", 0)]
    assert session_start_index(ws, WORK, DIST) == 2


def test_hops_collapse_and_truncate():
    assert hops(["a", "a", "b", "a", "c", "c", "d", "e"], 4) == ["a", "c", "d", "e"]


def test_body_uses_samples_but_never_excluded_private_or_locked():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SAMPLES_DDL)
    rows = [
        (T0 + 10, "Code", None, 0, 0, 0),  # work window 0
        (T0 + 130, "Safari", "youtube.com", 0, 0, 0),
        (T0 + 140, "1Password", None, 0, 1, 0),  # excluded
        (T0 + 150, "Safari", "secret.com", 1, 0, 0),  # private
        (T0 + 160, "Safari", "locked.com", 0, 0, 1),  # locked
        (T0 + 250, "Safari", "reddit.com", 0, 0, 0),
        (T0 + 370, "Safari", "youtube.com", 0, 0, 0),
    ]
    conn.executemany("INSERT INTO samples (ts, app_name, domain, private, excluded, locked) VALUES (?,?,?,?,?,?)", rows)
    ws = [W(T0, "Coding", 0), W(T0 + 120, "Video", 0), W(T0 + 240, "Social", 0), W(T0 + 360, "Video", 0)]
    body, start = build_body(conn, ws, CFG)
    assert body == "This looks like a rabbit hole: youtube.com -> reddit.com -> youtube.com, 6 min in. Head back to Code?"
    assert start == T0 + 120
    for bad in ("1Password", "secret", "locked"):
        assert bad not in body


# ---------------------------------------------------------------- engine


def _db(bridge=True):
    conn = sqlite3.connect(":memory:")
    if bridge:
        conn.executescript(BRIDGE_DDL)
    return conn


class Store:
    def __init__(self, wins):
        self.wins = wins

    def load_day(self, conn, day):
        return list(self.wins)


def _risky_day(n):
    return [W(T0 + 120 * i, "Video", 6) for i in range(n)]


def _run(conn, wins, now):
    r = LiveRunner(conn, Store(wins).load_day)
    rows = r.tick("d")
    return on_tick(conn, "d", rows, wins, now=now, cfg=CFG)


def test_engine_inserts_response_row_then_outbox_row():
    conn = _db()
    wins = _risky_day(8)
    now = wins[-1].window_start + 130
    res = _run(conn, wins, now)
    assert res and not res["holdout"]
    nr = conn.execute("SELECT id, nudge_id, asked_at, response, window_start, window_end FROM nudge_responses").fetchall()
    assert len(nr) == 1 and nr[0][1] == res["nudge_id"] and nr[0][3] is None
    assert nr[0][5] == wins[-1].window_start + 120
    ob = conn.execute("SELECT kind, ref_id, payload_json, expires_at, created_at FROM notify_outbox").fetchall()
    assert ob[0][0] == "nudge" and ob[0][1] == nr[0][0]
    payload = json.loads(ob[0][2])
    assert payload["body"].startswith("This looks like a rabbit hole") and payload["nudge_id"] == res["nudge_id"]
    assert ob[0][3] == pytest.approx(ob[0][4] + 60)


def test_engine_is_idempotent_and_ignores_stale_windows():
    conn = _db()
    wins = _risky_day(8)
    now = wins[-1].window_start + 130
    assert _run(conn, wins, now)
    assert on_tick(conn, "d", [{"window_start": wins[-1].window_start}], wins, now=now, cfg=CFG) is None
    assert conn.execute("SELECT COUNT(*) FROM notify_outbox").fetchone()[0] == 1
    # replay long after the fact: no nudge
    conn2 = _db()
    assert _run(conn2, wins, now + 3600) is None
    assert conn2.execute("SELECT COUNT(*) FROM notify_outbox").fetchone()[0] == 0


def test_engine_respects_cooldown_from_db():
    conn = _db()
    wins = _risky_day(8)
    assert _run(conn, wins, wins[-1].window_start + 130)
    more = _risky_day(12)
    r = LiveRunner(conn, Store(more).load_day)
    rows = r.tick("d")
    assert on_tick(conn, "d", rows, more, now=more[-1].window_start + 130, cfg=CFG) is None  # 4 windows later


def test_engine_snooze_blocks_for_20_minutes():
    conn = _db()
    wins = _risky_day(8)
    now = wins[-1].window_start + 130
    conn.execute(
        "INSERT INTO nudge_responses (nudge_id, asked_at, responded_at, response) VALUES ('x', ?, ?, 'snooze')", (now - 60, now - 30)
    )
    assert _run(conn, wins, now) == {"skipped": "snoozed"}


def test_engine_skips_without_bridge_tables():
    conn = _db(bridge=False)
    wins = _risky_day(8)
    assert _run(conn, wins, wins[-1].window_start + 130) == {"skipped": "bridge tables missing"}


def test_holdout_logged_not_shown_and_counts_for_cooldown():
    conn = _db()
    wins = _risky_day(8)
    r = LiveRunner(conn, Store(wins).load_day)
    rows = r.tick("d")
    res = on_tick(conn, "d", rows, wins, now=wins[-1].window_start + 130, cfg={**CFG, "holdout_frac": 1.0})
    assert res["holdout"] is True
    assert conn.execute("SELECT COUNT(*) FROM notify_outbox").fetchone()[0] == 0
    assert conn.execute("SELECT holdout FROM nudge_log").fetchone()[0] == 1


def test_holdout_is_deterministic_and_about_ten_percent():
    xs = [is_holdout(T0 + 120 * i, 0.1) for i in range(5000)]
    assert xs == [is_holdout(T0 + 120 * i, 0.1) for i in range(5000)]
    assert 0.08 < sum(xs) / len(xs) < 0.12
