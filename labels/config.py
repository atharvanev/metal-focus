"""Auto-labeler thresholds from labels/config.yaml (never hard-coded)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).with_name("config.yaml")


@dataclass(frozen=True)
class LabelsConfig:
    work_categories: tuple[str, ...]
    distraction_categories: tuple[str, ...]
    BREAK_MAX: int
    RABBIT_MIN: int
    HOPS_MIN: int
    GAP_OK: int
    DRIFT_MAX: int


def load_labels_config(path: Path | None = None) -> LabelsConfig:
    d = yaml.safe_load((path or DEFAULT_PATH).read_text())
    cfg = LabelsConfig(
        work_categories=tuple(d["work_categories"]),
        distraction_categories=tuple(d["distraction_categories"]),
        **{k: int(d[k]) for k in ("BREAK_MAX", "RABBIT_MIN", "HOPS_MIN", "GAP_OK", "DRIFT_MAX")},
    )
    if not (0 < cfg.BREAK_MAX < cfg.RABBIT_MIN) or cfg.HOPS_MIN < 2 or cfg.GAP_OK < 0 or cfg.DRIFT_MAX < 1:
        raise ValueError(f"inconsistent thresholds in {path or DEFAULT_PATH}")
    return cfg
