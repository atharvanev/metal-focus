"""Live nudge engine: runs as a hook after each hsmm.runner tick.

All state is rebuilt from the DB on every call (restart-safe):
- history: today's rows in `readouts` (model='hsmm')
- cooldown: latest decision in `nudge_log` (owned here)
- snooze: latest 'snooze' in `nudge_responses` (written by the bridge)

Only the newest window is considered, and only if it ended within
`recency_s`, so replays after a restart or a day rebuild never send stale
nudges. The decision for a window is recorded once (nudge_log PRIMARY KEY
window_start, INSERT OR IGNORE) in the same transaction as the
nudge_responses + notify_outbox inserts.

`notify_outbox` and `nudge_responses` belong to the bridge (bridge/schema.sql);
this module never creates them. If they are missing, nudging is skipped.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Sequence

from nudge.rule import Point, load_config, should_fire
from nudge.text import build_body

NUDGE_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS nudge_log (
    window_start      REAL PRIMARY KEY,  -- window the decision was made on
    day               TEXT NOT NULL,
    decided_at        REAL NOT NULL,
    nudge_id          TEXT NOT NULL,
    risk              REAL NOT NULL,
    holdout           INTEGER NOT NULL,  -- 1 = fired but deliberately not shown
    nudge_response_id INTEGER,           -- nudge_responses.id when shown
    body              TEXT
);
"""

BRIDGE_TABLES = ("notify_outbox", "nudge_responses")


def _tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def is_holdout(window_start: float, frac: float) -> bool:
    """Deterministic per window, so a replayed decision is the same decision."""
    h = hashlib.sha256(f"holdout:{window_start:.0f}".encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64 < frac


def _snoozed(conn, now: float, cfg: dict) -> bool:
    row = conn.execute("SELECT MAX(responded_at) FROM nudge_responses WHERE response = 'snooze'").fetchone()
    return bool(row and row[0] is not None and now < row[0] + cfg["snooze_minutes"] * 60)


def on_tick(conn, day: str, rows: Sequence[dict], windows: Sequence[Any], now: float | None = None, cfg: dict | None = None):
    """Runner hook. Returns a dict describing the decision, or None."""
    if not rows or not windows:
        return None
    cfg = cfg or load_config()
    now = time.time() if now is None else now
    latest = rows[-1]
    ws = float(latest["window_start"])
    if now - (ws + cfg["window_s"]) > cfg["recency_s"]:
        return None  # backfill / replay: never act on old windows
    tables = _tables(conn)
    if not all(t in tables for t in BRIDGE_TABLES):
        return {"skipped": "bridge tables missing"}
    conn.executescript(NUDGE_LOG_SCHEMA)
    if _snoozed(conn, now, cfg):
        return {"skipped": "snoozed"}

    hist = [
        Point(r[0], json.loads(r[1])["risk"], bool(json.loads(r[1]).get("away")))
        for r in conn.execute(
            "SELECT window_start, readout FROM readouts WHERE model='hsmm' AND day=? AND window_start<=? "
            "ORDER BY window_start",
            (day, ws),
        )
    ]
    last = conn.execute("SELECT MAX(window_start) FROM nudge_log WHERE window_start < ?", (ws,)).fetchone()[0]
    if not should_fire(hist, last, cfg):
        return None

    holdout = is_holdout(ws, cfg["holdout_frac"])
    upto = [w for w in windows if float(w.window_start) <= ws]
    body, session_start = build_body(conn, upto, cfg)
    nudge_id = str(uuid.uuid4())
    risk = hist[-1].risk
    with conn:  # one transaction
        cur = conn.execute(
            "INSERT OR IGNORE INTO nudge_log (window_start, day, decided_at, nudge_id, risk, holdout, body) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ws, day, now, nudge_id, risk, int(holdout), body),
        )
        if cur.rowcount == 0:
            return None  # already decided for this window
        if holdout:
            return {"nudge_id": nudge_id, "holdout": True, "body": body}
        window_end = ws + cfg["window_s"]
        nr = conn.execute(
            "INSERT INTO nudge_responses (nudge_id, asked_at, window_start, window_end) VALUES (?, ?, ?, ?)",
            (nudge_id, now, session_start, window_end),
        )
        nr_id = nr.lastrowid
        payload = {"body": body, "nudge_id": nudge_id, "window_start": session_start, "window_end": window_end, "risk": risk}
        conn.execute(
            "INSERT INTO notify_outbox (created_at, kind, ref_id, payload_json, expires_at) VALUES (?, 'nudge', ?, ?, ?)",
            (now, nr_id, json.dumps(payload), now + cfg["expires_s"]),
        )
        conn.execute("UPDATE nudge_log SET nudge_response_id = ? WHERE window_start = ?", (nr_id, ws))
    return {"nudge_id": nudge_id, "holdout": False, "body": body, "nudge_response_id": nr_id}
