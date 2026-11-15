"""Hindsight auto-labeler (rules, not ML): a day's windows -> one state label (or None) per window.

Distraction session: starts at a distraction window (Chat/Video/Social) and extends over distraction
windows. A run of <= GAP_OK non-distraction, non-Away windows (work, Other or Excluded) between two
distraction windows is a blip and stays inside the session. Away, or a longer non-distraction run,
ends the session. Other/Excluded never extend a session on their own: an hour of Excluded
(e.g. confidential work) after a distraction must never become a rabbit hole.
- ended by a return to work, <= BREAK_MAX windows -> Short break
- >= RABBIT_MIN windows or >= HOPS_MIN distinct distraction sites (any ending) -> Drifting (first
  ~1/3, max DRIFT_MAX) then Rabbit hole
- anything else (gray zone, or ended by Away / Other / Excluded / end of data first) -> None
Work windows outside sessions -> Working; Away -> Away; Other/Excluded outside sessions -> None.
"""

from __future__ import annotations

from typing import Sequence

from data.windows import Window
from labels.config import LabelsConfig

WORKING, SHORT_BREAK, DRIFTING, RABBIT_HOLE, AWAY = "Working", "Short break", "Drifting", "Rabbit hole", "Away"


def drift_split(n: int, cfg: LabelsConfig) -> list[str]:
    """Labels for an n-window rabbit hole: Drifting lead-in then Rabbit hole."""
    n_drift = min(cfg.DRIFT_MAX, max(1, round(n / 3)))
    return [DRIFTING] * min(n_drift, n) + [RABBIT_HOLE] * max(0, n - n_drift)


def window_kinds(windows: Sequence[Window], cfg: LabelsConfig) -> list[str]:
    """A = Away, W = work, D = distraction, N = neutral (Other/Excluded/anything else).

    Hindsight: an Other window directly leading into a distraction window with the same dominant
    app is that stretch's lead-in (data/windows.py escalates long Spotify runs only from the Nth
    window on), so it counts as D too.
    """
    kind = ["A" if w.is_away else "W" if w.category in cfg.work_categories
            else "D" if w.category in cfg.distraction_categories else "N" for w in windows]
    for t in range(len(windows) - 2, -1, -1):
        dom = getattr(windows[t], "dominant", None)  # nudge/ passes duck-typed windows
        if kind[t] == "N" and kind[t + 1] == "D" and windows[t].category == "Other" and dom \
                and dom == getattr(windows[t + 1], "dominant", None):
            kind[t] = "D"
    return kind


def auto_label(windows: Sequence[Window], cfg: LabelsConfig) -> list[str | None]:
    n = len(windows)
    kind = window_kinds(windows, cfg)
    labels: list[str | None] = [AWAY if k == "A" else WORKING if k == "W" else None for k in kind]
    t = 0
    while t < n:
        if kind[t] != "D":
            t += 1
            continue
        start = end = t  # session covers start..end inclusive
        u = t + 1
        ending = "cutoff"
        while u < n:
            if kind[u] == "D":
                end = u
                u += 1
            elif kind[u] == "A":
                ending = "away"
                break
            else:  # run of work/neutral windows: a blip if short and followed by more distraction
                v = u
                while v < n and kind[v] in "WN":
                    v += 1
                if v - u <= cfg.GAP_OK and v < n and kind[v] == "D":
                    end = v
                    u = v + 1
                else:
                    ending = "returned" if kind[u] == "W" else "neutral"
                    break
        span = windows[start : end + 1]
        hops = len({w.dominant for w in span if w.category in cfg.distraction_categories and w.dominant})
        length = end - start + 1
        if length >= cfg.RABBIT_MIN or hops >= cfg.HOPS_MIN:
            session: list[str | None] = drift_split(length, cfg)
        elif ending == "returned" and length <= cfg.BREAK_MAX:
            session = [SHORT_BREAK] * length
        else:
            session = [None] * length
        labels[start : end + 1] = session
        t = end + 1
    return labels
