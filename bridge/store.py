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
