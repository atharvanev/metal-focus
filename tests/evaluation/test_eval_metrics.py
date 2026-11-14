from dataclasses import dataclass

import pytest

from eval.metrics import checkin_truth, grade_episodes, grade_nudges, rabbit_hole_episodes
from eval.run import build_models, evaluate
from hsmm.config import load_config
from hsmm.simulate import default_true_params, sample_days

W_, SB, DR, RH = 0, 1, 2, 3


def test_grade_nudges_counts_only_labeled_windows():
    truth = [[W_, DR, None, RH, SB], [None, None]]
    fires = [[1, 2, 4], [0]]
    g = grade_nudges(fires, truth)
    assert (g.n_fired, g.n_graded, g.false_alarms) == (4, 2, 1)
    assert g.precision == 0.5 and g.false_alarms_per_day == 0.5


def test_episodes_need_a_rabbit_hole_window():
    assert rabbit_hole_episodes([W_, DR, DR, W_, DR, RH, RH, None, RH]) == [(4, 7), (8, 9)]


def test_lead_time_relative_to_20_minute_mark():
    day = [W_] + [DR] * 3 + [RH] * 10 + [W_]  # episode starts at 1; 20-min mark at index 11
    g = grade_episodes([[3]], [day])
    assert (g.n_episodes, g.caught) == (1, 1)
    assert g.lead_minutes == [pytest.approx((1 + 10 - 3) * 2.0)]
    assert grade_episodes([[0]], [day]).caught == 0  # fired before the episode: not a catch
    assert g.rabbit_hole_minutes_per_week == pytest.approx(13 * 2.0 * 7)


@dataclass(frozen=True)
class C:  # mimics labels.checkins.EvalCheckin
    window_start: float
    window_end: float
    response: str


def test_checkin_truth_covers_half_open_range_and_skips_away():
    states = load_config()["states"]
    days = [[0.0, 120.0, 240.0, 360.0]]
    cs = [C(120.0, 360.0, "Drifting"), C(360.0, 480.0, "Away")]
    assert checkin_truth(days, cs, states) == [[None, DR, DR, None]]


# ---------------------------------------------------------------- synthetic end-to-end


@dataclass(frozen=True)
class W:
    window_start: float
    category: str
    switch_count: int
    is_away: int = 0


CATS = ["Coding", "Docs", "Chat", "Video", "Social"]


def _windows(d, t0):
    return [W(t0 + 120 * i, CATS[c], [0, 3, 6][s]) for i, (c, s) in enumerate(zip(d.cat, d.sw))]


def test_evaluate_on_synthetic_days_with_known_truth():
    true = default_true_params()
    hcfg = load_config()
    train = sample_days(true, 10, 240, seed=31)
    held = sample_days(true, 5, 240, seed=32)
    models = build_models(train, held, hcfg)
    eval_windows = [_windows(d, 1e6 + 86400 * i) for i, d in enumerate(held)]
    truth = [list(d.states) for d in held]
    rep = evaluate(models, eval_windows, truth_primary=truth, truth_secondary=truth)
    h, m = rep["models"]["hsmm"], rep["models"]["hmm"]
    assert h["loglik_per_window"] > m["loglik_per_window"]  # duration model pays off
    assert h["primary_checkins"]["precision"] > 0.8  # observed ~0.9+ on synthetic
    ep = h["secondary_merged_labels"]["episodes"]
    assert ep["n_episodes"] > 0 and ep["caught"] / ep["n_episodes"] > 0.6
    assert ep["median_lead_minutes"] is not None
