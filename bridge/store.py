"""The bridge's only SQL. Reads for the viewer, outbox relay, and response writes.

Opens the logger's DB only via ``data.db.open_existing`` (never creates a DB or key).
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import sqlcipher3

DB_ERRORS: tuple[type[BaseException], ...] = (sqlcipher3.Error, sqlite3.Error)
_OPERATIONAL = (sqlcipher3.OperationalError, sqlite3.OperationalError)
_SCHEMA = Path(__file__).with_name("schema.sql")
AWAY_IDLE_S = 90.0
READOUT_MODEL = "hsmm"


def _running_threshold_s() -> float:
    from logger.config import load_config

    return 3 * load_config().sample_interval_s


def _local_day_bounds(day: str) -> tuple[float, float]:
    start = datetime.strptime(day, "%Y-%m-%d")
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


class Store:
    def __init__(self, conn: Any, now: Callable[[], float] = time.time):
        self.conn = conn
        self.now = now
        self.running_threshold_s = _running_threshold_s()
        conn.executescript(_SCHEMA.read_text())
        conn.commit()

    @classmethod
    def open(cls, db_path: Path | None = None, key_store: Any = None) -> "Store":
        from data.db import open_existing

        return cls(open_existing(db_path, key_store))

    # -- helpers ---------------------------------------------------------
    def _rows(self, sql: str, args: tuple = ()) -> list[dict]:
        cur = self.conn.execute(sql, args)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def _today_start(self) -> float:
        return datetime.fromtimestamp(self.now()).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    # -- reads -------------------------------------------------------------
    def status(self) -> dict:
        last_ts = self.conn.execute("SELECT max(ts) FROM samples").fetchone()[0]
        n, n_excl = self.conn.execute(
            "SELECT count(*), coalesce(sum(excluded), 0) FROM samples WHERE ts >= ?", (self._today_start(),)
        ).fetchone()
        ev = self._rows("SELECT ts, kind, detail FROM events ORDER BY ts DESC LIMIT 1")
        return {
            "logger_running": last_ts is not None and self.now() - last_ts < self.running_threshold_s,
            "last_sample_ts": last_ts,
            "samples_today": n,
            "excluded_today": n_excl,
            "last_event": ev[0] if ev else None,
        }

    def samples(self, since: float | None = None, limit: int = 200) -> list[dict]:
        rows = self._rows(
            "SELECT * FROM samples WHERE ts > ? ORDER BY ts DESC LIMIT ?",
            (since if since is not None else float("-inf"), int(limit)),
        )
        for r in rows:
            if r["excluded"] == 1 or r["private"] == 1:
                r.update(window_title=None, domain=None, app_name=None, bundle_id=None)
        return rows

    def events(self, since: float | None = None, limit: int = 100) -> list[dict]:
        return self._rows(
            "SELECT id, ts, kind, detail FROM events WHERE ts > ? ORDER BY ts DESC LIMIT ?",
            (since if since is not None else float("-inf"), int(limit)),
        )

    def checkins(self, limit: int = 50) -> list[dict]:
        return self._rows("SELECT * FROM checkins ORDER BY asked_at DESC LIMIT ?", (int(limit),))

    def timeline(self, day: str) -> list[dict]:
        """Segments of consecutive same-label samples (source='samples')."""
        lo, hi = _local_day_bounds(day)
        rows = self.conn.execute(
            "SELECT ts, dt, app_name, excluded, locked, idle_s FROM samples WHERE ts >= ? AND ts < ? ORDER BY ts",
            (lo, hi),
        ).fetchall()
        segs: list[dict] = []
        for ts, dt, app, excluded, locked, idle in rows:
            if excluded == 1:
                label = "Excluded"
            elif locked == 1 or (idle or 0) >= AWAY_IDLE_S:
                label = "Away"
            else:
                label = app or "Unknown"
            if segs and segs[-1]["label"] == label:
                segs[-1]["end"] = ts + dt
            else:
                segs.append({"start": ts, "end": ts + dt, "label": label, "source": "samples"})
        return segs

    def readout(self, max_age_s: float = 300) -> dict | None:
        try:
            row = self.conn.execute(
                "SELECT window_start, day, written_at, readout FROM readouts WHERE model = ? "
                "ORDER BY window_start DESC LIMIT 1",
                (READOUT_MODEL,),
            ).fetchone()
        except _OPERATIONAL:
            return None
        if row is None or row[2] is None or self.now() - row[2] > max_age_s:
            return None
        return {"window_start": row[0], "day": row[1], "written_at": row[2], **json.loads(row[3])}

    # -- outbox + responses ------------------------------------------------
    def pending_outbox(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, kind, ref_id, expires_at, payload_json FROM notify_outbox "
            "WHERE delivered_at IS NULL AND expires_at > ? ORDER BY created_at, id",
            (self.now(),),
        ).fetchall()
        return [
            {"outbox_id": i, "kind": k, "ref_id": ref, "expires_at": exp, "payload": json.loads(pj or "{}")}
            for i, k, ref, exp, pj in rows
        ]

    def ack_delivered(self, outbox_id: int) -> bool:
        """Mark shown; the referenced label row's asked_at becomes the shown time."""
        now = self.now()
        with self.conn:
            row = self.conn.execute("SELECT kind, ref_id FROM notify_outbox WHERE id = ?", (outbox_id,)).fetchone()
            if row is None:
                return False
            kind, ref = row
            self.conn.execute("UPDATE notify_outbox SET delivered_at = ? WHERE id = ?", (now, outbox_id))
            if ref is not None:
                self.conn.execute(f"UPDATE {_TABLE[kind]} SET asked_at = ? WHERE id = ?", (now, ref))
        return True

    def checkin_response(self, checkin_id: int, response: str, responded_at: float) -> bool:
        if response not in CHECKIN_RESPONSES:
            raise ValueError(f"invalid check-in response {response!r}")
        with self.conn:
            cur = self.conn.execute(
                "UPDATE checkins SET response = ?, responded_at = ? WHERE id = ? AND response IS NULL",
                (response, responded_at, checkin_id),
            )
        return cur.rowcount == 1

    def nudge_response(self, nudge_row_id: int, response: str, responded_at: float) -> bool:
        if response not in NUDGE_RESPONSES:
            raise ValueError(f"invalid nudge response {response!r}")
        with self.conn:
            cur = self.conn.execute(
                "UPDATE nudge_responses SET response = ?, responded_at = ?, response_latency_s = ? - asked_at "
                "WHERE id = ? AND response IS NULL",
                (response, responded_at, responded_at, nudge_row_id),
            )
        return cur.rowcount == 1

    def sweep_expired(self, grace_s: float = 30) -> int:
        """Shown-but-unanswered prompts past expires_at + grace become 'dismissed' (responded_at = expires_at)."""
        cutoff = self.now() - grace_s
        n = 0
        with self.conn:
            for kind, table in _TABLE.items():
                cur = self.conn.execute(
                    f"UPDATE {table} SET response = 'dismissed', "
                    f"responded_at = (SELECT o.expires_at FROM notify_outbox o "
                    f"  WHERE o.kind = ? AND o.ref_id = {table}.id AND o.delivered_at IS NOT NULL "
                    f"  AND o.expires_at < ? ORDER BY o.expires_at DESC LIMIT 1) "
                    f"WHERE response IS NULL AND id IN (SELECT ref_id FROM notify_outbox "
                    f"  WHERE kind = ? AND ref_id IS NOT NULL AND delivered_at IS NOT NULL AND expires_at < ?)",
                    (kind, cutoff, kind, cutoff),
                )
                n += cur.rowcount
        return n

    def insert_test_prompt(self, kind: str, ttl_s: float = 60) -> int:
        if kind not in _TABLE:
            raise ValueError(kind)
        payload = {"test": True}
        payload["question" if kind == "checkin" else "body"] = (
            DEFAULT_QUESTION if kind == "checkin" else "Test nudge: this looks like a rabbit hole. Head back?"
        )
        now = self.now()
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO notify_outbox (created_at, kind, ref_id, payload_json, expires_at) VALUES (?, ?, NULL, ?, ?)",
                (now, kind, json.dumps(payload), now + ttl_s),
            )
        return cur.lastrowid


DEFAULT_QUESTION = "What were you doing the last 10 min?"
CHECKIN_RESPONSES = frozenset({"Working", "Short break", "Drifting", "Rabbit hole", "Away", "skip", "dismissed"})
NUDGE_RESPONSES = frozenset({"back_to_work", "on_break", "snooze", "dismissed"})
_TABLE = {"checkin": "checkins", "nudge": "nudge_responses"}
