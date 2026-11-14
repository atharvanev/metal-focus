"""Held-out metrics (pure functions over per-day window sequences).

truth: per day, a list aligned with the windows: state index
(0 Working, 1 Short break, 2 Drifting, 3 Rabbit hole) or None (unknown/away).
fires: per day, window indices where the nudge rule fired.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Sequence

WORKING, SHORT_BREAK, DRIFTING, RABBIT_HOLE = range(4)
POSITIVE = {DRIFTING, RABBIT_HOLE}
NEGATIVE = {WORKING, SHORT_BREAK}

Truth = Sequence[Sequence[int | None]]
Fires = Sequence[Sequence[int]]


@dataclass(frozen=True)
class NudgeGrade:
    precision: float | None  # TP / graded (None if nothing graded)
    n_fired: int
    n_graded: int  # fires that landed on a labeled window
    false_alarms: int  # fires on windows labeled Working / Short break
    # Only counts fires that land on labeled windows. With sparse truth (~1 random
    # check-in/day) this is a LOWER bound; nudges_per_day is the upper bound.
    false_alarms_on_labeled_per_day: float


def grade_nudges(fires: Fires, truth: Truth) -> NudgeGrade:
    tp = fp = fired = 0
    for f_day, t_day in zip(fires, truth):
        for i in f_day:
            fired += 1
            s = t_day[i] if i < len(t_day) else None
            if s in POSITIVE:
                tp += 1
            elif s in NEGATIVE:
                fp += 1
    graded = tp + fp
    n_days = max(len(fires), 1)
    return NudgeGrade(tp / graded if graded else None, fired, graded, fp, fp / n_days)


def rabbit_hole_episodes(day: Sequence[int | None]) -> list[tuple[int, int]]:
    """[start, end) runs of Drifting/Rabbit hole that contain at least one Rabbit hole window."""
    eps, i, n = [], 0, len(day)
    while i < n:
        if day[i] in POSITIVE:
            j = i
            while j < n and day[j] in POSITIVE:
                j += 1
            if any(day[k] == RABBIT_HOLE for k in range(i, j)):
                eps.append((i, j))
            i = j
        else:
            i += 1
    return eps


@dataclass(frozen=True)
class EpisodeGrade:
    n_episodes: int
    caught: int
    lead_minutes: list[float]  # per caught episode: + = before the rabbit-hole mark
    median_lead_minutes: float | None
    rabbit_hole_minutes_per_week: float


def grade_episodes(fires: Fires, truth: Truth, window_minutes: float = 2.0, rabbit_min_windows: int = 10) -> EpisodeGrade:
    n = caught = 0
    leads: list[float] = []
    rh_windows = 0
    for f_day, t_day in zip(fires, truth):
        fset = sorted(f_day)
        for s, e in rabbit_hole_episodes(t_day):
            n += 1
            rh_windows += e - s
            inside = [i for i in fset if s <= i < e]
            if inside:
                caught += 1
                leads.append((s + rabbit_min_windows - inside[0]) * window_minutes)
    weeks = max(len(truth), 1) / 7.0
    return EpisodeGrade(
        n_episodes=n,
        caught=caught,
        lead_minutes=leads,
        median_lead_minutes=statistics.median(leads) if leads else None,
        rabbit_hole_minutes_per_week=rh_windows * window_minutes / weeks,
    )


def checkin_truth(windows_by_day: Sequence[Sequence[float]], checkins, states: Sequence[str]) -> list[list[int | None]]:
    """Per-window truth from eval check-ins: each covers [window_start, window_end).

    windows_by_day: per day, the window_start of each window.
    Away responses stay None (Away is never graded).
    """
    out = [[None] * len(d) for d in windows_by_day]
    for c in checkins:
        if c.response not in states:
            continue
        s = states.index(c.response)
        for di, day in enumerate(windows_by_day):
            for wi, ws in enumerate(day):
                if c.window_start <= ws < c.window_end:
                    out[di][wi] = s
    return out
