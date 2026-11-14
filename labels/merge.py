"""Nightly label merge: auto-labels, overlaid by nudge responses, overlaid by train-split check-ins.

The auto-labeler is never modified by human labels. Precedence: check-in > nudge response > auto.
Only label-bearing responses count: nudge 'back_to_work' (the session was a rabbit hole: Drifting
lead-in then Rabbit hole) and 'on_break' (Short break). Check-in responses that are states. Skips,
dismissals, snoozes and unanswered rows are ignored. Eval-split check-ins never reach training.
A response covers windows with window_start in [row.window_start, row.window_end).
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from data.windows import Window, load_day
from labels.autolabel import SHORT_BREAK, auto_label, drift_split
from labels.config import LabelsConfig, load_labels_config

STATES = ("Working", "Short break", "Drifting", "Rabbit hole", "Away")
Label = tuple[str | None, str | None]


def _covered(windows: Sequence[Window], start: float | None, end: float | None) -> list[int]:
    if start is None or end is None:
        return []
    return [i for i, w in enumerate(windows) if start <= w.window_start < end]


def merge_labels(
    windows: Sequence[Window],
    auto: Sequence[str | None],
    nudges: Iterable[Mapping[str, Any]],
    checkins: Iterable[Mapping[str, Any]],
    cfg: LabelsConfig,
) -> list[Label]:
    out: list[Label] = [(a, "auto" if a is not None else None) for a in auto]
    for n in nudges:
        idx = _covered(windows, n.get("window_start"), n.get("window_end"))
        if n.get("response") == "back_to_work":
            for i, state in zip(idx, drift_split(len(idx), cfg)):
                out[i] = (state, "nudge")
        elif n.get("response") == "on_break":
            for i in idx:
                out[i] = (SHORT_BREAK, "nudge")
    for c in checkins:
        if c.get("split") != "train" or c.get("response") not in STATES:
            continue
        for i in _covered(windows, c.get("window_start"), c.get("window_end")):
            out[i] = (c["response"], "checkin")
    return out


def _rows(conn, sql: str, params: tuple) -> list[dict]:
    try:
        cur = conn.execute(sql, params)
    except Exception as e:  # table not created yet (fresh DB)
        if "no such table" in str(e):
            return []
        raise
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur]


def load_training_labels(conn, day: str, now: float | None = None, cfg: LabelsConfig | None = None) -> list[Label]:
    """Per-window (state, source) for local day ``day``, aligned 1:1 with ``data.windows.load_day``."""
    cfg = cfg or load_labels_config()
    windows = load_day(conn, day, now=now)
    if not windows:
        return []
    lo, hi = windows[0].window_start, windows[-1].window_start + 120
    q = "SELECT response, window_start, window_end{} FROM {} WHERE window_end > ? AND window_start < ? ORDER BY {}"
    nudges = _rows(conn, q.format("", "nudge_responses", "asked_at"), (lo, hi))
    checkins = _rows(conn, q.format(", split", "checkins", "asked_at"), (lo, hi))
    return merge_labels(windows, auto_label(windows, cfg), nudges, checkins, cfg)
