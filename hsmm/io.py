"""Adapter from the logger's windows / merged labels to HSMM inputs.

This is the ONLY module that knows the windowing/label schema. Agreed
contract with the logger branch (proposal until data/windows.py lands):
- data.windows.load_day(conn, day) -> list[Window]; Window has
  .category (str), .switch_count (int), .is_away (bool/int), ...
- labels.merge.load_training_labels(conn, day) -> list[(state | None, source | None)]
  aligned 1:1 with the windows, eval-split check-ins already excluded.
Categories outside hsmm/config.yaml `categories` (e.g. Other, Excluded) are
missing observations (emission factor 1).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from hsmm.batch import HARD, SOFT, Batch, DayObs, build_batch
from hsmm.config import load_config

SOURCE_CLAMP = {"checkin": HARD, "nudge": HARD, "auto": SOFT}


def switch_bin(count: int | None, cfg: dict) -> int:
    if count is None:
        return -1
    b = cfg["switch_bins"]
    return 0 if count <= b["low_max"] else 1 if count <= b["med_max"] else 2


def category_index(category: str | None, cfg: dict) -> int:
    try:
        return cfg["categories"].index(category)
    except ValueError:
        return -1


def window_to_obs(w: Any, cfg: dict) -> tuple[int, int, bool]:
    """(category index, switch bin, is_away) for one window-like object."""
    return (
        category_index(getattr(w, "category", None), cfg),
        switch_bin(getattr(w, "switch_count", None), cfg),
        bool(getattr(w, "is_away", False)),
    )


def day_to_obs(
    windows: Sequence[Any],
    labels: Sequence[tuple[str | None, str | None]] | None = None,
    cfg: dict | None = None,
) -> DayObs:
    cfg = cfg or load_config()
    states = cfg["states"]
    lw = cfg["labels"]
    weights_by_source = {"checkin": lw["checkin_weight"], "nudge": lw["nudge_weight"], "auto": 1.0}
    cat, sw, away = zip(*(window_to_obs(w, cfg) for w in windows)) if windows else ((), (), ())
    label, clamp, weight = [], [], []
    for i in range(len(windows)):
        state, source = labels[i] if labels is not None else (None, None)
        if state in states and source in SOURCE_CLAMP:
            label.append(states.index(state))
            clamp.append(SOURCE_CLAMP[source])
            weight.append(float(weights_by_source[source]))
        else:  # None, gray zone, or Away (covered by the is_away mask)
            label.append(-1)
            clamp.append(None)
            weight.append(1.0)
    return DayObs(cat=list(cat), sw=list(sw), away=list(away), label=label, clamp=clamp, weight=weight)


def days_to_batch(days: Sequence[DayObs], cfg: dict | None = None) -> Batch:
    cfg = cfg or load_config()
    return build_batch(
        list(days),
        K=len(cfg["states"]),
        away_break_windows=cfg["away_break_windows"],
        soft_on=cfg["labels"]["soft_on"],
        soft_off=cfg["labels"]["soft_off"],
        hard_off=cfg["labels"]["hard_off"],
    )


def load_training_batch(conn, days: Sequence[str], cfg: dict | None = None) -> Batch:
    """Nightly entry point. Imports lazily so hsmm/ works before data/ exists."""
    from data.windows import load_day  # type: ignore[import-not-found]
    from labels.merge import load_training_labels  # type: ignore[import-not-found]

    cfg = cfg or load_config()
    obs = [day_to_obs(load_day(conn, d), load_training_labels(conn, d), cfg) for d in days]
    return days_to_batch([o for o in obs if len(o)], cfg)
