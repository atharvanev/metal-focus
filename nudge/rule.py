"""The nudge rule, as one pure function shared by the live engine and eval/.

Fire when the last `consecutive_windows` non-away readouts all have
risk > threshold, the current window is not Away, and no decision was made in
the last `cooldown_windows` windows. Holdouts and dismissed nudges count as
decisions for the cooldown, so nudged and held-out firings stay comparable.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Sequence

import yaml

CONFIG_PATH = Path(__file__).with_name("config.yaml")


@lru_cache(maxsize=None)
def load_config(path: str | None = None) -> dict:
    with open(path or CONFIG_PATH) as f:
        return yaml.safe_load(f)


@dataclass(frozen=True)
class Point:
    window_start: float
    risk: float
    away: bool = False


def should_fire(history: Sequence[Point], last_decision_start: float | None, cfg: dict) -> bool:
    """Decide for history[-1] (the current window), given earlier points of the same day."""
    if not history or history[-1].away:
        return False
    cur = history[-1]
    if last_decision_start is not None:
        if cur.window_start - last_decision_start < cfg["cooldown_windows"] * cfg["window_s"]:
            return False
    k = cfg["consecutive_windows"]
    recent = [p for p in history if not p.away][-k:]
    return len(recent) == k and all(p.risk > cfg["risk_threshold"] for p in recent)


def replay(points: Iterable[Point], cfg: dict) -> list[int]:
    """Indices where the rule fires over a day's readouts (eval / offline use)."""
    hist: list[Point] = []
    fired: list[int] = []
    last = None
    for i, p in enumerate(points):
        hist.append(p)
        if should_fire(hist, last, cfg):
            fired.append(i)
            last = p.window_start
    return fired
