"""Read-only queries behind the local dashboard.

Every function takes an open DB connection and returns JSON-ready dicts. Nothing is written to the
DB or to disk: the dashboard computes windows and labels on the fly, exactly like the nightly jobs
(``data.windows.load_day`` + ``labels.merge.load_training_labels``).
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from data.windows import WINDOW_S, load_day
from labels.merge import load_training_labels

RUNNING_AFTER_S = 12  # a sample within this many seconds -> the sampler is running (3 x 4 s interval)
LOCAL_DAY = "date(ts + tz_offset_min * 60, 'unixepoch')"


def _has_table(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _rows(conn, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur]


def list_days(conn) -> list[str]:
    """Local days (YYYY-MM-DD) that have samples, newest first."""
    return [r[0] for r in conn.execute(f"SELECT DISTINCT {LOCAL_DAY} FROM samples ORDER BY 1 DESC")]


def _day_bounds(conn, day: str) -> tuple[float, float, int]:
    """UTC epoch bounds of local ``day`` and its tz offset (minutes), taken from that day's samples."""
    d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    wide_lo, wide_hi = (d - timedelta(hours=15)).timestamp(), (d + timedelta(days=1, hours=15)).timestamp()
    row = conn.execute(
        f"SELECT tz_offset_min FROM samples WHERE ts >= ? AND ts < ? AND {LOCAL_DAY} = ? ORDER BY ts DESC LIMIT 1",
        (wide_lo, wide_hi, day),
    ).fetchone()
    tz = row[0] if row else 0
    lo = d.timestamp() - tz * 60
    return lo, lo + 86400, tz


def _runs(states: list[str | None]) -> list[list[str | None]]:
    out: list[list[str | None]] = []
    for s in states:
        if out and out[-1][-1] == s:
            out[-1].append(s)
        else:
            out.append([s])
    return out


def day_view(conn, day: str, now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    lo, hi, tz = _day_bounds(conn, day)
    windows = load_day(conn, day, now=now)
    labels = load_training_labels(conn, day, now=now) if windows else []

    risk: dict[float, dict[str, Any]] = {}
    if _has_table(conn, "readouts"):
        for r in _rows(conn, "SELECT window_start, readout FROM readouts WHERE day = ? AND model = 'hsmm'", (day,)):
            ro = json.loads(r["readout"])
            risk[r["window_start"]] = {"risk": ro.get("risk"), "top_state": ro.get("top_state"),
                                       "top_prob": ro.get("top_prob"), "away": ro.get("away", False)}

    win_out = []
    for w, (state, source) in zip(windows, labels or [(None, None)] * len(windows)):
        r = risk.get(w.window_start)
        win_out.append({
            "start": w.window_start, "category": "Away" if w.is_away else w.category, "dominant": w.dominant,
            "low_confidence": w.low_confidence, "switches": w.switch_count, "idle_s": round(w.idle_seconds, 1),
            "covered_s": round(w.covered_seconds, 1), "typing": w.typing_frac, "click": w.click_frac,
            "scroll": w.scroll_frac, "media": w.media_frac, "state": state, "source": source,
            "risk": None if r is None or r["away"] else r["risk"],
        })

    minutes_by_category: dict[str, float] = defaultdict(float)
    minutes_by_state: dict[str, float] = defaultdict(float)
    for w in win_out:
        minutes_by_category[w["category"]] += WINDOW_S / 60
        minutes_by_state[w["state"] or "Unlabeled"] += WINDOW_S / 60
    rabbit_holes = sum(1 for run in _runs([w["state"] for w in win_out]) if run[0] == "Rabbit hole")

    in_day = f"ts >= ? AND ts < ? AND {LOCAL_DAY} = ?"
    p = (lo - 86400, hi + 86400, day)
    logged_s = conn.execute(f"SELECT COALESCE(SUM(dt), 0), COUNT(*) FROM samples WHERE {in_day}", p).fetchone()
    top_apps = _rows(conn, f"""
        SELECT CASE WHEN excluded = 1 THEN 'Excluded' ELSE COALESCE(app_name, bundle_id, 'Unknown') END AS name,
               SUM(dt) AS seconds
        FROM samples WHERE {in_day} AND locked = 0 GROUP BY name ORDER BY seconds DESC LIMIT 12""", p)
    top_domains = _rows(conn, f"""
        SELECT domain AS name, SUM(dt) AS seconds FROM samples
        WHERE {in_day} AND domain IS NOT NULL AND excluded = 0 GROUP BY domain ORDER BY seconds DESC LIMIT 12""", p)
    top_titles = _rows(conn, f"""
        SELECT COALESCE(app_name, bundle_id) AS app, window_title AS name, SUM(dt) AS seconds FROM samples
        WHERE {in_day} AND window_title IS NOT NULL AND excluded = 0
        GROUP BY app, window_title ORDER BY seconds DESC LIMIT 15""", p)

    checkins = _rows(conn, """
        SELECT id, asked_at, trigger, window_start, window_end, model_top_state, model_top_prob,
               response, responded_at, split
        FROM checkins WHERE asked_at >= ? AND asked_at < ? ORDER BY asked_at""", (lo, hi))
    nudges: list[dict[str, Any]] = []
    if _has_table(conn, "nudge_log"):
        nudges = _rows(conn, """
            SELECT l.window_start, l.risk, l.holdout, l.body, r.response, r.responded_at
            FROM nudge_log l LEFT JOIN nudge_responses r ON r.id = l.nudge_response_id
            WHERE l.day = ? ORDER BY l.window_start""", (day,))
    events = _rows(conn, "SELECT ts, kind, detail FROM events WHERE ts >= ? AND ts < ? ORDER BY ts", (lo, hi))

    return {
        "day": day, "tz_offset_min": tz, "day_start": lo, "window_s": WINDOW_S,
        "windows": win_out,
        "totals": {
            "logged_min": round(logged_s[0] / 60, 1), "samples": logged_s[1],
            "minutes_by_category": dict(minutes_by_category), "minutes_by_state": dict(minutes_by_state),
            "rabbit_holes": rabbit_holes,
            "checkins_asked": len(checkins),
            "checkins_answered": sum(1 for c in checkins if c["response"] not in (None, "skip", "dismissed")),
            "nudges": sum(1 for n in nudges if not n["holdout"]),
        },
        "top_apps": top_apps, "top_domains": top_domains, "top_titles": top_titles,
        "checkins": checkins, "nudges": nudges, "events": events,
        "has_readouts": bool(risk),
    }


def live(conn, limit: int = 30, now: float | None = None) -> dict[str, Any]:
    """The newest samples (excluded/private rows already carry no title or domain) and sampler status."""
    now = time.time() if now is None else now
    samples = _rows(conn, """
        SELECT ts, tz_offset_min, app_name, bundle_id, window_title, domain, private, excluded, fullscreen,
               locked, idle_s, app_switches, key_active, click_active, scroll_active, media_playing
        FROM samples ORDER BY ts DESC LIMIT ?""", (limit,))
    last = samples[0]["ts"] if samples else None
    events = _rows(conn, "SELECT ts, kind, detail FROM events ORDER BY ts DESC LIMIT 10")
    return {
        "now": now, "last_sample": last, "running": last is not None and now - last <= RUNNING_AFTER_S,
        "samples": samples, "events": events,
        # Reported by the app when it last started (it can be granted later without a new event).
        "permission_missing_at_start": next((e["detail"] for e in events if e["kind"] == "permission_missing"
                                    and not any(x["kind"] == "logger_start" and x["ts"] > e["ts"] for x in events)),
                                   None),
    }
