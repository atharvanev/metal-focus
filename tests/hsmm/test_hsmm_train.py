import mlx.core as mx
import pytest

from hsmm.batch import HARD, DayObs, build_batch
from hsmm.train import apply_update, expected_counts

PRIOR = {"B": 20.0, "C": 20.0, "A": 10.0, "duration": 10.0, "pi": 10.0}


def _all_state0(weight):
    # 4 windows hard-clamped to state 0 (D = 4 so one stretch fits)
    return DayObs(cat=[0, 0, 1, 2], sw=[0, 1, 0, 0], label=[0] * 4, clamp=[HARD] * 4, weight=[weight] * 4)


@pytest.mark.parametrize("weight", [1.0, 3.0])
def test_evidence_weighted_update_by_hand(tiny_params, weight):
    P = tiny_params
    c = expected_counts(P, build_batch([_all_state0(weight)], K=3))
    new = apply_update(P, c, PRIOR, floor=0.0)
    # B: (20 * B_today + soft counts) / (20 + time in state); counts are exact under hard clamps
    counts0 = mx.array([2.0, 1.0, 1.0]) * weight
    expected_B0 = (20 * P.B[0] + counts0) / (20 + 4 * weight)
    assert new.B[0].tolist() == pytest.approx(expected_B0.tolist(), abs=1e-5)
    expected_C0 = (20 * P.C[0] + mx.array([3.0, 1.0]) * weight) / (20 + 4 * weight)
    assert new.C[0].tolist() == pytest.approx(expected_C0.tolist(), abs=1e-5)
    # pi: one start, in state 0
    expected_pi = (10 * P.pi + mx.array([1.0, 0.0, 0.0])) / 11
    assert new.pi.tolist() == pytest.approx(expected_pi.tolist(), abs=1e-5)


def test_unvisited_states_and_uncompleted_stretches_do_not_change(tiny_params):
    P = tiny_params
    new = apply_update(P, expected_counts(P, build_batch([_all_state0(1.0)], K=3)), PRIOR, floor=0.0)
    assert mx.array_equal(new.B[1:], P.B[1:]).item()
    assert mx.array_equal(new.C[1:], P.C[1:]).item()
    # no switch happened (single censored stretch): A and durations unchanged
    assert mx.allclose(new.A, P.A).item()
    assert mx.array_equal(new.mu, P.mu).item() and mx.array_equal(new.r, P.r).item()


def test_eval_free_batch_counts_weighting(tiny_params):
    """Check-in weight 3 triples that window's contribution to the counts."""
    P = tiny_params
    c1 = expected_counts(P, build_batch([_all_state0(1.0)], K=3))
    c3 = expected_counts(P, build_batch([_all_state0(3.0)], K=3))
    assert mx.allclose(c3.B, 3 * c1.B).item()


def test_em_increases_loglik(tiny_params):
    from hsmm.simulate import sample_days
    from hsmm.train import fit_em

    days = sample_days(tiny_params, 5, 40, seed=3)
    _, hist = fit_em(tiny_params, build_batch(days, K=3), n_iter=8, tol=0.0)
    assert all(b >= a - 1e-2 for a, b in zip(hist, hist[1:]))


def test_impossible_hard_labels_stay_finite():
    """A nudge/check-in labeling 70 min of Rabbit hole (> D = 60 min) must not produce NaN/inf."""
    from hsmm.simulate import default_true_params

    P = default_true_params()
    day = DayObs(cat=[3] * 35, sw=[0] * 35, label=[3] * 35, clamp=[HARD] * 35, weight=[3.0] * 35)
    c = expected_counts(P, build_batch([day], K=4))
    assert mx.isfinite(mx.array(c.loglik)).item()
    for arr in (c.B, c.C, c.trans, c.dur, c.pi):
        assert mx.all(mx.isfinite(arr)).item()
    new = apply_update(P, c, PRIOR)
    for arr in (new.A, new.B, new.C, new.mu, new.r, new.pi):
        assert mx.all(mx.isfinite(arr)).item()


def test_zero_fitted_transition_with_hard_label_stays_finite():
    from hsmm.simulate import default_true_params

    P = default_true_params()  # A[Working -> Rabbit hole] = 0 before flooring
    labels = [0] * 5 + [3] * 5
    day = DayObs(cat=[0] * 5 + [3] * 5, sw=[0] * 10, label=labels, clamp=[HARD] * 10)
    c = expected_counts(P, build_batch([day], K=4))
    assert mx.all(mx.isfinite(c.B)).item() and mx.isfinite(mx.array(c.loglik)).item()


def test_underflowed_sequence_is_skipped_not_nan(tiny_params):
    """Exactly-zero hard clamp (hard_off=0) on an impossible path: dropped with a warning."""
    P = tiny_params  # D = 4
    bad = DayObs(cat=[0] * 6, sw=[0] * 6, label=[0] * 6, clamp=[HARD] * 6)
    good = DayObs(cat=[0, 1, 2, 0], sw=[0, 1, 0, 1])
    with pytest.warns(RuntimeWarning, match="skipped"):
        c = expected_counts(P, build_batch([bad, good], K=3, hard_off=0.0))
    ref = expected_counts(P, build_batch([good], K=3))
    assert mx.allclose(c.B, ref.B, atol=1e-6).item()
    assert c.loglik == pytest.approx(ref.loglik, abs=1e-4)
