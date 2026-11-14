"""Live HSMM: one forward step per window on the CPU stream."""

from __future__ import annotations

from typing import Any, Sequence

import mlx.core as mx

from common.device import live_stream
from common.model_api import Readout
from hsmm import forward as F
from hsmm.config import load_config
from hsmm.io import day_to_obs, days_to_batch, window_to_obs
from hsmm.params import HSMMParams


class LiveHSMM:
    """Implements common.model_api.LiveModel and TrainableModel."""

    def __init__(self, params: HSMMParams, cfg: dict | None = None):
        self.cfg = cfg or load_config()
        self.set_params(params)
        self.reset()

    def set_params(self, params: HSMMParams) -> None:
        self.params = params
        with live_stream():
            self.der = params.derived()
            mx.eval(self.der.p, self.der.h, self.der.Bx, self.der.Cx)

    def reset(self) -> None:
        self.alpha = mx.zeros((1, self.params.K, self.params.D))
        self.started = False
        self.away_run = 0
        self.last: Readout | None = None

    def step(self, window: Any) -> Readout:
        cat, sw, away = window_to_obs(window, self.cfg)
        return self.step_obs(cat, sw, away)

    def step_obs(self, cat: int, sw: int, away: bool = False) -> Readout:
        if away:
            self.away_run += 1
            base = self.last or Readout(risk=0.0)
            return Readout(**{**base.__dict__, "away": True})
        restart = (not self.started) or self.away_run >= self.cfg["away_break_windows"]
        self.started, self.away_run = True, 0
        with live_stream():
            e = F.evidence(self.der, mx.array([cat]), mx.array([sw]))
            self.alpha, logc = F.forward_step(self.der, self.alpha, e, mx.array([True]), mx.array([restart]))
            a = self.alpha[0]
            ps = F.state_probs(a)
            elapsed = F.expected_elapsed(a)
            haz = F.expected_hazard(self.der, a)
            ks = self.cfg["forecast_windows"]
            fc = [F.forecast(self.der, self.alpha, k)[0] for k in ks]
            mx.eval(self.alpha, logc, ps, elapsed, haz, *fc)  # end of live step
        ps_l = ps.tolist()
        top = max(range(len(ps_l)), key=ps_l.__getitem__)
        risk = sum(ps_l[j] for j in F.RISK_STATES)
        self.last = Readout(
            risk=risk,
            p_state=tuple(ps_l),
            top_state=top,
            top_prob=ps_l[top],
            minutes_in_state=elapsed[top].item() * self.cfg["window_minutes"],
            hazard=haz[top].item(),
            forecast_risk={k: sum(f[j].item() for j in F.RISK_STATES) for k, f in zip(ks, fc)},
            surprise=-logc.item(),
        )
        return self.last

    def fit(self, days: Sequence[Sequence[Any]], labels: Sequence[Any] | None = None) -> None:
        """Nightly update from raw windows (+ merged labels) of training days."""
        from hsmm.train import nightly_update

        obs = [day_to_obs(d, labels[i] if labels else None, self.cfg) for i, d in enumerate(days)]
        new, _ = nightly_update(self.params, days_to_batch(obs, self.cfg), self.cfg)
        self.set_params(new)
