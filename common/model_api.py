"""Model-agnostic interface shared by the HSMM, baselines, and the later GRU/hybrid.

nudge/ and eval/ depend only on this module, never on a concrete model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class Readout:
    """What a live model reports after each 2-min window.

    `risk` (P(Drifting) + P(Rabbit hole), or P(rabbit hole) for the GRU) is
    the only required field. The HSMM fills the rest; these past-only
    outputs are also the hybrid model's input features.
    """

    risk: float
    p_state: tuple[float, ...] | None = None  # Working, Short break, Drifting, Rabbit hole
    top_state: int | None = None
    top_prob: float | None = None
    minutes_in_state: float | None = None  # expected elapsed minutes in top state
    hazard: float | None = None  # P(current stretch ends now | top state)
    forecast_risk: dict[int, float] = field(default_factory=dict)  # windows ahead -> risk
    surprise: float | None = None  # -log c_t
    # Window was Away: belief frozen and the previous readout is carried over.
    # nudge/ must not count Away readouts toward "risk > threshold for N windows".
    away: bool = False

    def hybrid_features(self) -> list[float]:
        """Flat feature vector of the HSMM outputs for the hybrid GRU."""
        ps = list(self.p_state or ())
        fr = [self.forecast_risk[k] for k in sorted(self.forecast_risk)]
        return ps + [self.risk, self.minutes_in_state or 0.0, self.hazard or 0.0, self.surprise or 0.0] + fr


@runtime_checkable
class LiveModel(Protocol):
    """Online model: one step per window, past-only."""

    def reset(self) -> None: ...

    def step(self, window: Any) -> Readout: ...


@runtime_checkable
class TrainableModel(Protocol):
    """Nightly fit on training days only (never eval days)."""

    def fit(self, days: Sequence[Any], labels: Sequence[Any] | None = None) -> None: ...
