import time
from dataclasses import dataclass

import mlx.core as mx
import pytest

from common.model_api import LiveModel, Readout, TrainableModel
from hsmm.config import load_config
from hsmm.forward import forward, state_probs
from hsmm.io import day_to_obs, days_to_batch, switch_bin
from hsmm.live import LiveHSMM
from hsmm.simulate import default_true_params, sample_days


@dataclass
class W:  # mimics data.windows.Window (only the fields hsmm/io.py reads)
    category: str
    switch_count: int
    is_away: int = 0


def test_switch_bins_and_categories():
    cfg = load_config()
    assert [switch_bin(n, cfg) for n in (0, 1, 2, 4, 5, 20)] == [0, 0, 1, 1, 2, 2]
    obs = day_to_obs([W("Coding", 0), W("Other", 3), W("Excluded", 9), W("Video", 1, 1)], cfg=cfg)
    assert obs.cat == [0, -1, -1, 3]
    assert obs.sw == [0, 1, 2, 0]
    assert obs.away == [False, False, False, True]


def test_label_sources_map_to_clamp_and_weight():
    cfg = load_config()
    wins = [W("Coding", 0)] * 4
    labels = [("Working", "auto"), ("Rabbit hole", "checkin"), ("Away", "auto"), (None, None)]
    obs = day_to_obs(wins, labels, cfg)
    assert obs.label == [0, 3, -1, -1]
    assert obs.clamp == ["soft", "hard", None, None]
    assert obs.weight == [1.0, 3.0, 1.0, 1.0]
    lf = days_to_batch([obs], cfg).label_factor[0].tolist()
    assert lf[0] == pytest.approx([0.9, 0.1, 0.1, 0.1])
    assert lf[1] == pytest.approx([1e-6, 1e-6, 1e-6, 1.0])  # hard: tiny off, never exactly 0


def test_live_matches_batch_forward():
    P = default_true_params()
    day = sample_days(P, 1, 60, seed=4, away_prob=0.05)[0]
    model = LiveHSMM(P)
    assert isinstance(model, LiveModel) and isinstance(model, TrainableModel)
    outs = [model.step_obs(c, s, a) for c, s, a in zip(day.cat, day.sw, day.away)]
    ref = state_probs(forward(P.derived(), days_to_batch([day])).alpha[:, 0]).tolist()
    for o, r in zip(outs, ref):
        assert list(o.p_state) == pytest.approx(r, abs=1e-5)
    assert all(isinstance(o, Readout) for o in outs)
    o = outs[-1]
    assert o.risk == pytest.approx(o.p_state[2] + o.p_state[3])
    assert set(o.forecast_risk) == {1, 3, 5}
    assert len(o.hybrid_features()) == 4 + 4 + 3


def test_away_never_raises_risk():
    P = default_true_params()
    m = LiveHSMM(P)
    for _ in range(4):
        r = m.step(W("Video", 1))
    frozen = [m.step(W("Video", 0, is_away=1)).risk for _ in range(10)]
    assert all(x == pytest.approx(r.risk) for x in frozen)
    assert m.last.minutes_in_state == r.minutes_in_state


def test_live_step_latency():
    m = LiveHSMM(default_true_params())
    for _ in range(5):
        m.step_obs(0, 0)
    n = 200
    t0 = time.perf_counter()
    for i in range(n):
        m.step_obs(i % 5, i % 3)
    per_step_ms = (time.perf_counter() - t0) / n * 1e3
    print(f"live step: {per_step_ms:.3f} ms")
    assert per_step_ms < 20  # windows are 2 min apart; this is a sanity bound


def test_fit_from_windows_runs():
    P = default_true_params()
    m = LiveHSMM(P)
    wins = [[W("Coding", 0)] * 20 + [W("Video", 2)] * 12 + [W("Docs", 1)] * 10]
    m.fit(wins)
    assert mx.all(mx.abs(m.params.B.sum(1) - 1) < 1e-5).item()
