"""Hindsight auto-labeler (rules, not ML): a day's windows -> one state label (or None) per window.

Distraction session: contiguous non-work, non-Away windows, allowing work blips of <= GAP_OK windows.
A session is labeled only if it contains at least one distraction window (Chat/Video/Social); sessions
made only of neutral windows (Other/Excluded) stay unlabeled.
- returned to work, <= BREAK_MAX windows, < HOPS_MIN sites  -> Short break
- >= RABBIT_MIN windows or >= HOPS_MIN distinct distraction sites (any ending) -> Drifting (first
  ~1/3, max DRIFT_MAX) then Rabbit hole
- anything else (gray zone, or cut off by Away / end of data before qualifying) -> None
Work windows outside sessions -> Working. Away windows -> Away (they also end a session).
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


def auto_label(windows: Sequence[Window], cfg: LabelsConfig) -> list[str | None]:
    n = len(windows)
    kind = ["A" if w.is_away else "W" if w.category in cfg.work_categories else "D" for w in windows]
    labels: list[str | None] = [AWAY if k == "A" else WORKING if k == "W" else None for k in kind]
    t = 0
    while t < n:
        if kind[t] != "D":
            t += 1
            continue
        start, end = t, t  # session covers start..end inclusive
        u = t + 1
        ending = "cutoff"
        while u < n:
            if kind[u] == "D":
                end = u
                u += 1
            elif kind[u] == "A":
                ending = "away"
                break
            else:  # work run: a blip if short and followed by more non-work
                v = u
                while v < n and kind[v] == "W":
                    v += 1
                if v - u <= cfg.GAP_OK and v < n and kind[v] == "D":
                    end = v
                    u = v + 1
                else:  # back at work (even if the day or the logging ends right after)
                    ending = "returned"
                    break
        span = windows[start : end + 1]
        distracting = [w for w in span if w.category in cfg.distraction_categories]
        hops = len({w.dominant for w in distracting if w.dominant})
        length = end - start + 1
        if not distracting:
            session = [None] * length
        elif length >= cfg.RABBIT_MIN or hops >= cfg.HOPS_MIN:
            session = drift_split(length, cfg)
        elif ending == "returned" and length <= cfg.BREAK_MAX:
            session = [SHORT_BREAK] * length
        else:
            session = [None] * length
        labels[start : end + 1] = session
        t = end + 1
    return labels
