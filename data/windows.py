"""Windowing: raw 4-second samples -> fixed 2-minute windows (the unit every model works on).

A window is aligned to the local clock (12:00, 12:02, ...) and belongs to the local calendar day it
starts in. Each sample is assigned to the window containing its timestamp and weighted by its ``dt``.

Contract (shared with hsmm/io.py, hsmm/runner.py, labels/): ``load_day`` returns only *completed*
windows, contiguous from the day's first sample to its last one, with gaps emitted as Away windows.
During a day the list only grows. Field names are fixed; tell the hsmm session before renaming any.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from labels.categorizer import Rules, categorize, is_escalating, load_rules

WINDOW_S = 120
AWAY_IDLE_S = 90  # spec: a window with 90+ s idle is Away
MIN_COVERED_S = 60  # less than this much logged time in a window (lid closed, logger down) -> Away
LOCKED_AWAY_S = 60  # screen locked for most of the window -> Away
_SAMPLE_COLS = ("ts", "tz_offset_min", "dt", "bundle_id", "window_title", "domain", "excluded", "locked", "media_playing",
                "idle_s", "app_switches", "key_active", "click_active", "scroll_active")


@dataclass(frozen=True)
class Window:
    window_start: float  # epoch s (UTC)
    tz_offset_min: int
    day: str  # local YYYY-MM-DD
    category: str  # Coding | Docs | Chat | Video | Social | Other | Excluded
    switch_count: int
    idle_seconds: float
    covered_seconds: float
    is_away: bool
    typing_frac: float | None  # fraction of measured samples with any typing (no input hooks)
    click_frac: float | None
    scroll_frac: float | None
    dominant: str | None  # domain (browsers) or bundle id that won the category; None if excluded/empty
    low_confidence: bool  # categorizer unsure about the dominant app/site (ambiguous-app trigger)
    media_frac: float | None = None  # fraction of measured samples with video playing in the frontmost app


def _local_index(ts: float, tz_min: int) -> int:
    return math.floor((ts + tz_min * 60) / WINDOW_S)


def _local_day(index: int) -> str:
    return datetime.fromtimestamp(index * WINDOW_S, tz=timezone.utc).strftime("%Y-%m-%d")


def _frac(values: list[Any]) -> float | None:
    measured = [v for v in values if v is not None]
    return sum(measured) / len(measured) if measured else None


def _summarize(index: int, tz: int, rows: list[dict], rules: Rules) -> Window:
    covered = min(WINDOW_S, sum(r["dt"] for r in rows))
    # Idle time while the frontmost app keeps the display awake (video playing) is watching, not Away.
    idle = min(WINDOW_S, sum(min(r["dt"], r["idle_s"]) for r in rows
                             if r["idle_s"] is not None and r.get("media_playing") != 1))
    locked = sum(r["dt"] for r in rows if r["locked"])
    by_cat: dict[tuple[str, bool, str | None], float] = defaultdict(float)
    for r in rows:
        if r["locked"]:
            continue
        c = categorize(r["bundle_id"], r["domain"], r["window_title"], rules, excluded=bool(r["excluded"]))
        key = None if r["excluded"] else (r["domain"] or r["bundle_id"])
        by_cat[(c.category, c.low_confidence, key)] += r["dt"]
    if by_cat:
        totals: dict[str, float] = defaultdict(float)
        for (cat, _, _), secs in by_cat.items():
            totals[cat] += secs
        category = max(totals, key=totals.get)
        (_, low, dominant), _ = max(((k, v) for k, v in by_cat.items() if k[0] == category), key=lambda kv: kv[1])
    else:
        category, low, dominant = "Other", False, None
    return Window(
        window_start=index * WINDOW_S - tz * 60,
        tz_offset_min=tz,
        day=_local_day(index),
        category=category,
        switch_count=sum(r["app_switches"] for r in rows),
        idle_seconds=idle,
        covered_seconds=covered,
        is_away=idle >= AWAY_IDLE_S or covered < MIN_COVERED_S or locked >= LOCKED_AWAY_S,
        typing_frac=_frac([r["key_active"] for r in rows]),
        click_frac=_frac([r["click_active"] for r in rows]),
        scroll_frac=_frac([r["scroll_active"] for r in rows]),
        dominant=dominant,
        low_confidence=low,
        media_frac=_frac([r.get("media_playing") for r in rows]),
    )


def build_windows(samples: Iterable[dict], day: str, now: float, rules: Rules) -> list[Window]:
    """Pure core of ``load_day``: samples (dicts with the ``samples`` columns) -> that day's windows."""
    buckets: dict[int, list[dict]] = defaultdict(list)
    tz_of: dict[int, int] = {}
    for r in samples:
        idx = _local_index(r["ts"], r["tz_offset_min"])
        if _local_day(idx) != day:
            continue
        buckets[idx].append(r)
        tz_of.setdefault(idx, r["tz_offset_min"])
    if not buckets:
        return []
    first, last = min(buckets), max(buckets)
    out: list[Window] = []
    tz = tz_of[first]
    for idx in range(first, last + 1):
        tz = tz_of.get(idx, tz)
        if idx * WINDOW_S - tz * 60 + WINDOW_S > now:
            break  # not completed yet
        out.append(_summarize(idx, tz, buckets.get(idx, []), rules))
    return _escalate(out, rules)


def _escalate(windows: list[Window], rules: Rules) -> list[Window]:
    """Long stretches on an escalating app (Spotify) become a distraction category.

    The after_windows-th and later consecutive non-Away Other windows dominated by an escalating
    app get ``rules.escalate_category``. Backward-looking only, so live and nightly agree.
    """
    if rules.escalate_category is None or rules.escalate_after <= 0:
        return windows
    run = 0
    out: list[Window] = []
    for w in windows:
        run = run + 1 if (not w.is_away and w.category == "Other" and is_escalating(w.dominant, rules)) else 0
        out.append(replace(w, category=rules.escalate_category, low_confidence=False)
                   if run >= rules.escalate_after else w)
    return out


def load_day(conn, day: str, now: float | None = None, rules: Rules | None = None) -> list[Window]:
    """Completed 2-minute windows for local day ``day`` (YYYY-MM-DD), computed from ``samples``."""
    d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    lo = (d - timedelta(hours=15)).timestamp()  # covers every UTC offset (-12..+14)
    hi = (d + timedelta(days=1, hours=15)).timestamp()
    cur = conn.execute(f"SELECT {', '.join(_SAMPLE_COLS)} FROM samples WHERE ts >= ? AND ts < ? ORDER BY ts", (lo, hi))
    rows = [dict(zip(_SAMPLE_COLS, r)) for r in cur]
    return build_windows(rows, day, time.time() if now is None else now, rules or load_rules())
