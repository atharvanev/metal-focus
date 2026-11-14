"""GRU / hybrid model: interface stub only. Do not build until several weeks of data exist.

Contract (see common.model_api): implements LiveModel (reset, step(window) -> Readout)
and TrainableModel (fit(days, labels)). Readout.risk = P(rabbit hole).

Planned inputs per window (all from the logger's windows table):
- category (one-hot over hsmm/config.yaml categories, plus missing)
- switch_count, idle_seconds, is_away
- typing_frac, click_frac, scroll_frac (fraction of samples with activity, 0..1, may be NULL)
- time of day, breaks this hour, minutes worked before (derived)
Hybrid variant additionally takes the HSMM's past-only Readout.hybrid_features()
from an HSMM fit on training days only.

Implementation notes for later: MLX (mlx.nn.GRU); live single-step forward on
common.device.live_stream(); batched training on train_stream() (GPU only if benchmarked).
"""

from __future__ import annotations

from typing import Any, Sequence

from common.model_api import Readout

FEATURES = (
    "category",
    "switch_count",
    "idle_seconds",
    "is_away",
    "typing_frac",
    "click_frac",
    "scroll_frac",
)


class GRUModel:
    def __init__(self, hybrid: bool = False):
        self.hybrid = hybrid

    def reset(self) -> None:
        raise NotImplementedError("GRU is deferred until several weeks of data exist")

    def step(self, window: Any) -> Readout:
        raise NotImplementedError("GRU is deferred until several weeks of data exist")

    def fit(self, days: Sequence[Any], labels: Sequence[Any] | None = None) -> None:
        raise NotImplementedError("GRU is deferred until several weeks of data exist")
