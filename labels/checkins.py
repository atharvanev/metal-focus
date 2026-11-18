"""Check-in scheduler: decides when to ask "What were you doing the last 10 min?".

Triggers in priority order: uncertain (live HSMM unsure) > gray_zone (a 10-20 min distraction
session) > ambiguous_app (first 10+ min of the day on a low-confidence app/site) > random (one
uniformly timed prompt a day, split='eval', never used for training). Suppressed during quiet
hours, meetings, fullscreen, focused work and right after a nudge; limited by a daily budget.

``decide`` is pure. ``tick`` builds its inputs from the database and, if it decides to ask,
writes the ``checkins`` row (response NULL) and the ``notify_outbox`` row in one transaction.
The bridge shows the prompt and records the response. Settings live in labels/config.yaml.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from data.windows import WINDOW_S, Window, load_day
from labels.config import DEFAULT_PATH, LabelsConfig, load_labels_config
from logger.privacy import domain_matches

log = logging.getLogger("labels.checkins")
STATE_NAMES = ("Working", "Short break", "Drifting", "Rabbit hole")
LABEL_STATES = STATE_NAMES + ("Away",)


@dataclass(frozen=True)
class CheckinConfig:
    question: str
    covers_windows: int
    expires_s: float
    budget: Mapping[str, Any]
    uncertain: Mapping[str, Any]
    gray_zone_windows: tuple[int, int]
    away_return: Mapping[str, Any]
    ambiguous_app_windows: int
    random_hours: tuple[int, int]
    suppress: Mapping[str, Any]
    labels: LabelsConfig = field(repr=False)


def load_checkin_config(path: Path | None = None) -> CheckinConfig:
    d = yaml.safe_load((path or DEFAULT_PATH).read_text())["checkins"]
    return CheckinConfig(
        question=d["question"], covers_windows=int(d["covers_windows"]), expires_s=float(d["expires_s"]),
        budget=d["budget"], uncertain=d["uncertain"], gray_zone_windows=tuple(d["gray_zone_windows"]),
        away_return=d["away_return"],
        ambiguous_app_windows=int(d["ambiguous_app_windows"]), random_hours=tuple(d["random_hours"]),
        suppress=d["suppress"], labels=load_labels_config(path),
    )


@dataclass(frozen=True)
class CheckinRequest:
    trigger: str
    window_start: float
    window_end: float
    split: str
    model_top_state: str | None
    model_top_prob: float | None
    question: str | None = None  # None -> cfg.question


@dataclass(frozen=True)
class Context:
    now: float
    tz_offset_min: int
    windows: Sequence[Window]  # today's completed windows
    readouts: Sequence[Mapping[str, Any] | None]  # aligned with windows; HSMM readout dicts
    current: Mapping[str, Any] | None  # latest sample: bundle_id, domain, fullscreen
    history: Sequence[Mapping[str, Any]]  # past check-ins: asked_at, trigger, response, window_start, window_end
    last_nudge_at: float | None
    days_logging: int  # 0 on the first day of logging
    random_at: float | None  # today's random-prompt time (see random_time)


def _tz(tz_min: int) -> timezone:
    return timezone(timedelta(minutes=tz_min))


def _day(ts: float, tz_min: int) -> str:
    return datetime.fromtimestamp(ts, _tz(tz_min)).strftime("%Y-%m-%d")


def random_time(day: str, tz_min: int, cfg: CheckinConfig) -> float:
    """Today's random prompt time: uniform within random_hours, stable for a given day."""
    seed = int.from_bytes(hashlib.sha256(f"metal-focus-random:{day}".encode()).digest()[:8], "big")
    h0, h1 = cfg.random_hours
    midnight = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=_tz(tz_min)).timestamp()
    return midnight + random.Random(seed).uniform(h0 * 3600, h1 * 3600)


def _suppressed(ctx: Context, cfg: CheckinConfig) -> str | None:
    s = cfg.suppress
    hour = datetime.fromtimestamp(ctx.now, _tz(ctx.tz_offset_min)).hour
    q0, q1 = s["quiet_hours"]
    if q0 <= hour < q1:
        return "quiet_hours"
    if ctx.windows and ctx.windows[-1].is_away:
        return "away"
    if ctx.last_nudge_at is not None and ctx.now - ctx.last_nudge_at < s["after_nudge_min"] * 60:
        return "after_nudge"
    cur = ctx.current or {}
    if s.get("fullscreen") and cur.get("fullscreen"):
        return "fullscreen"
    if cur.get("bundle_id") in s["meeting_bundle_ids"] or (
        cur.get("domain") and any(domain_matches(cur["domain"], d) for d in s["meeting_domains"])
    ):
        return "meeting"
    n = int(s["focus_windows"])
    tail = list(ctx.windows[-n:])
    if len(tail) == n and all(not w.is_away and w.category in cfg.labels.work_categories for w in tail):
        typing = [w.typing_frac for w in tail if w.typing_frac is not None]
        if typing and sum(typing) / len(typing) >= s["focus_min_typing_frac"]:
            return "focus"
    return None


def _daily_limit(ctx: Context, cfg: CheckinConfig) -> int:
    b = cfg.budget
    limit = b["week1"] if ctx.days_logging < 7 else b["weeks2_3"] if ctx.days_logging < 21 else b["later"]
    answered = [h for h in sorted(ctx.history, key=lambda h: h["asked_at"])
                if h.get("response") in LABEL_STATES + ("skip",)]
    streak = answered[-b["skip_streak"]:]
    if len(streak) == b["skip_streak"] and all(h["response"] == "skip" for h in streak):
        last = datetime.strptime(_day(streak[-1]["asked_at"], ctx.tz_offset_min), "%Y-%m-%d")
        today = datetime.strptime(_day(ctx.now, ctx.tz_offset_min), "%Y-%m-%d")
        if (today - last).days <= b["backoff_days"]:
            limit = min(limit, b["backoff_per_day"])
    return int(limit)


def _live_readouts(ctx: Context) -> list[Mapping[str, Any]]:
    return [r for r in ctx.readouts if r and not r.get("away") and r.get("p_state") and r.get("top_prob") is not None]


def _uncertain(ctx: Context, cfg: CheckinConfig) -> bool:
    u = cfg.uncertain
    live = _live_readouts(ctx)
    k = int(u["for_windows"])
    if len(live) >= k and all(r["top_prob"] < u["top_prob_below"] for r in live[-k:]):
        return True
    if live:
        p = live[-1]["p_state"]
        return abs(p[1] - p[2]) < u["sb_vs_drift_within"] and max(p[1], p[2]) >= max(p[0], p[3])
    return False


def _open_session_length(ctx: Context, cfg: CheckinConfig) -> int:
    """Length (windows) of the distraction session still running at the end of today's windows.

    Same session rule as labels/autolabel.py: only distraction windows extend a session; a run of
    <= GAP_OK work/Other/Excluded windows between distraction windows is a blip.
    """
    from labels.autolabel import window_kinds

    kind = window_kinds(ctx.windows, cfg.labels)
    t = len(kind) - 1
    if t < 0 or kind[t] != "D":
        return 0
    start = t
    while t >= 0:
        if kind[t] == "D":
            start = t
            t -= 1
            continue
        if kind[t] == "A":
            break
        u = t
        while u >= 0 and kind[u] in "WN":
            u -= 1
        if t - u > cfg.labels.GAP_OK or u < 0 or kind[u] != "D":
            break
        t = u
    return len(kind) - start


def _covered_key(ctx: Context, h: Mapping[str, Any]) -> str | None:
    keys = [w.dominant for w in ctx.windows
            if h.get("window_start") is not None and h["window_start"] <= w.window_start < h["window_end"]]
    return max(set(keys), key=keys.count) if keys else None


def _ambiguous(ctx: Context, cfg: CheckinConfig, today: list[Mapping[str, Any]]) -> bool:
    if not ctx.windows:
        return False
    last = ctx.windows[-1]
    if not last.low_confidence or not last.dominant or last.is_away:
        return False
    total = sum(1 for w in ctx.windows if w.dominant == last.dominant and w.low_confidence and not w.is_away)
    if total < cfg.ambiguous_app_windows:
        return False
    return not any(h["trigger"] == "ambiguous_app" and _covered_key(ctx, h) == last.dominant for h in today)


def _away_return(ctx: Context, cfg: CheckinConfig, history: Sequence[Mapping[str, Any]]) -> tuple[int, int] | None:
    """(first lead-in index, last away index) when the user just came back from a long Away that
    followed a distraction stretch and hasn't been asked about it; else None."""
    from labels.autolabel import window_kinds

    a = cfg.away_return
    kind = window_kinds(ctx.windows, cfg.labels)
    t = len(kind)
    while t and kind[t - 1] != "A":  # windows since coming back
        t -= 1
    back = len(kind) - t
    if not (1 <= back <= a["within_windows"]):
        return None
    away_end = t - 1
    while t and kind[t - 1] == "A":
        t -= 1
    if away_end - t + 1 < a["min_away_windows"]:
        return None
    lead_end = t
    while t and kind[t - 1] == "D":
        t -= 1
    if lead_end - t < a["lead_windows"]:
        return None
    start = ctx.windows[t].window_start
    if any(h["trigger"] == "away_return" and h.get("window_start") == start for h in history):
        return None
    return t, away_end


def decide(ctx: Context, cfg: CheckinConfig) -> CheckinRequest | None:
    if not ctx.windows or _suppressed(ctx, cfg):
        return None
    today = [h for h in ctx.history if _day(h["asked_at"], ctx.tz_offset_min) == _day(ctx.now, ctx.tz_offset_min)]
    if any(ctx.now - h["asked_at"] < cfg.budget["min_gap_min"] * 60 for h in ctx.history):
        return None
    limit = _daily_limit(ctx, cfg)
    if len(today) >= limit:
        return None
    random_done = any(h["trigger"] == "random" for h in today)
    reserve = cfg.budget["random_per_day"] if (not random_done and limit > 1) else 0
    trigger = None
    away = None
    if len(today) < limit - reserve:
        lo, hi = cfg.gray_zone_windows
        if _uncertain(ctx, cfg):
            trigger = "uncertain"
        elif (away := _away_return(ctx, cfg, ctx.history)) is not None:
            trigger = "away_return"
        elif lo < _open_session_length(ctx, cfg) < hi:  # passed 10 min, not yet 20
            trigger = "gray_zone"
        elif _ambiguous(ctx, cfg, today):
            trigger = "ambiguous_app"
    if trigger is None and not random_done and ctx.random_at is not None and ctx.random_at <= ctx.now:
        trigger = "random"
    if trigger is None:
        return None
    covered = ctx.windows[away[0]:away[1] + 1] if away else ctx.windows[-cfg.covers_windows:]
    live = _live_readouts(ctx)
    top = live[-1] if live else None
    return CheckinRequest(
        trigger=trigger,
        window_start=covered[0].window_start,
        window_end=covered[-1].window_start + WINDOW_S,
        split="eval" if trigger == "random" else "train",
        model_top_state=STATE_NAMES[top["top_state"]] if top else None,
        model_top_prob=top["top_prob"] if top else None,
        question=cfg.away_return["question"].format(minutes=sum(w.is_away for w in covered) * WINDOW_S // 60)
        if away else None,
    )


# ---------------------------------------------------------------- database side

def _rows(conn, sql: str, params: tuple = ()) -> list[dict]:
    try:
        cur = conn.execute(sql, params)
    except Exception as e:  # table not created yet
        if "no such table" in str(e):
            return []
        raise
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur]


def _current_tz(conn) -> int:
    row = _rows(conn, "SELECT tz_offset_min FROM samples ORDER BY ts DESC LIMIT 1")
    if row:
        return int(row[0]["tz_offset_min"])
    off = datetime.now().astimezone().utcoffset()
    return int(off.total_seconds() // 60) if off else 0


def build_context(conn, now: float, cfg: CheckinConfig) -> Context:
    tz = _current_tz(conn)
    today = _day(now, tz)
    windows = load_day(conn, today, now=now)
    by_start = {r["window_start"]: json.loads(r["readout"]) for r in _rows(
        conn, "SELECT window_start, readout FROM readouts WHERE model = 'hsmm' AND day = ?", (today,))}
    cur = _rows(conn, "SELECT bundle_id, domain, fullscreen FROM samples WHERE ts <= ? ORDER BY ts DESC LIMIT 1", (now,))
    first = _rows(conn, "SELECT min(ts) AS t FROM samples")
    first_ts = first[0]["t"] if first and first[0]["t"] is not None else now
    nudge = _rows(conn, "SELECT max(asked_at) AS t FROM nudge_responses WHERE asked_at <= ?", (now,))
    days_logging = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(_day(first_ts, tz), "%Y-%m-%d")).days
    return Context(
        now=now,
        tz_offset_min=tz,
        windows=windows,
        readouts=[by_start.get(w.window_start) for w in windows],
        current=cur[0] if cur else None,
        history=_rows(conn, "SELECT asked_at, trigger, response, window_start, window_end FROM checkins "
                            "WHERE asked_at <= ? ORDER BY asked_at", (now,)),
        last_nudge_at=nudge[0]["t"] if nudge else None,
        days_logging=days_logging,
        random_at=random_time(today, tz, cfg),
    )


def ask(conn, req: CheckinRequest, now: float, cfg: CheckinConfig) -> int:
    """Insert the checkins row (response NULL) and its notify_outbox row in one transaction."""
    try:
        cur = conn.execute(
            "INSERT INTO checkins (asked_at, trigger, window_start, window_end, model_top_state, model_top_prob, "
            "response, responded_at, split) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?)",
            (now, req.trigger, req.window_start, req.window_end, req.model_top_state, req.model_top_prob, req.split),
        )
        cid = cur.lastrowid
        conn.execute(
            "INSERT INTO notify_outbox (created_at, kind, ref_id, payload_json, delivered_at, expires_at) "
            "VALUES (?, 'checkin', ?, ?, NULL, ?)",
            (now, cid, json.dumps({"question": req.question or cfg.question}), now + cfg.expires_s),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return cid


def tick(conn, now: float | None = None, cfg: CheckinConfig | None = None) -> CheckinRequest | None:
    cfg = cfg or load_checkin_config()
    now = time.time() if now is None else now
    from data.windows import not_working_now

    if not_working_now(conn):
        return None
    req = decide(build_context(conn, now, cfg), cfg)
    if req is not None:
        cid = ask(conn, req, now, cfg)
        log.info("check-in %s asked (%s, split=%s)", cid, req.trigger, req.split)
    return req


@dataclass(frozen=True)
class EvalCheckin:
    id: int
    asked_at: float
    responded_at: float | None
    window_start: float
    window_end: float  # exclusive
    trigger: str
    response: str  # Working | Short break | Drifting | Rabbit hole | Away


def load_eval(conn, start_day: str, end_day: str) -> list[EvalCheckin]:
    """Answered eval-split (random) check-ins whose local asked day is in [start_day, end_day]."""
    tz = _current_tz(conn)
    rows = _rows(conn, "SELECT id, asked_at, responded_at, window_start, window_end, trigger, response FROM checkins "
                       "WHERE split = 'eval' ORDER BY asked_at")
    return [EvalCheckin(**r) for r in rows
            if r["response"] in LABEL_STATES and r["window_start"] is not None
            and start_day <= _day(r["asked_at"], tz) <= end_day]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m labels.checkins")
    p.add_argument("command", choices=["run", "once"])
    p.add_argument("--interval", type=float, default=30.0)
    args = p.parse_args(argv)
    from common.supervised import exit_with_parent

    exit_with_parent()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from data.db import KeyMissingError, WrongKeyError, open_existing

    cfg = load_checkin_config()
    conn = None
    while True:
        try:
            conn = conn or open_existing()
            from data.db import init_schema

            init_schema(conn)
            tick(conn, cfg=cfg)
        except (FileNotFoundError, KeyMissingError, WrongKeyError) as e:
            log.warning("cannot run yet: %s", e)
        except Exception:
            log.exception("tick failed; reconnecting")
            conn = None
        if args.command == "once":
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
