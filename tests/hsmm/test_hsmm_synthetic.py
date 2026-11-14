"""Validation on synthetic days sampled from known parameters."""

import mlx.core as mx
import pytest

from hsmm.backward import viterbi
from hsmm.batch import build_batch
from hsmm.forward import forward, state_probs
from hsmm.params import HSMMParams
from hsmm.simulate import default_true_params, sample_days
from hsmm.train import fit_em


@pytest.fixture(scope="module")
def truth():
    P = default_true_params()
    days = sample_days(P, 20, 240, seed=1)
    return P, days, build_batch(days, K=4)


def _accuracy(pred_rows, days):
    tot = ok = 0
    for row, d in zip(pred_rows, days):
        for p, s in zip(row, d.states):
            if s >= 0:
                tot += 1
                ok += p == s
    return ok / tot


def test_sampler_is_deterministic():
    P = default_true_params()
    a = sample_days(P, 2, 50, seed=7)
    b = sample_days(P, 2, 50, seed=7)
    assert [d.cat for d in a] == [d.cat for d in b] and [d.states for d in a] == [d.states for d in b]


def test_filtering_and_viterbi_accuracy(truth):
    P, days, batch = truth
    der = P.derived()
    filt = mx.argmax(state_probs(forward(der, batch).alpha), axis=-1).T.tolist()
    vit = [viterbi(der, batch, n) for n in range(len(days))]
    f_acc, v_acc = _accuracy(filt, days), _accuracy(vit, days)
    assert f_acc > 0.82  # observed ~0.88
    assert v_acc > 0.86  # observed ~0.92
    assert v_acc >= f_acc - 0.01  # hindsight should not be worse than filtering


def test_away_windows_do_not_hurt_filtering():
    P = default_true_params()
    days = sample_days(P, 10, 240, seed=2, away_prob=0.05)
    assert any(any(d.away) for d in days)
    batch = build_batch(days, K=4)
    filt = mx.argmax(state_probs(forward(P.derived(), batch).alpha), axis=-1).T.tolist()
    assert _accuracy(filt, days) > 0.82


@pytest.mark.slow
def test_parameter_recovery_from_perturbed_start(truth):
    P, _, batch = truth
    d0 = P.to_dict()
    uniform_A = [[0 if i == j else 1 / 3 for j in range(4)] for i in range(4)]
    init = HSMMParams.create(
        pi=[0.25] * 4,
        A=uniform_A,
        B=mx.array(d0["B"]) * 0.6 + 0.4 / 5,
        C=mx.array(d0["C"]) * 0.6 + 0.4 / 3,
        mu=[9, 4, 4, 11],
        r=[2, 2, 2, 2],
    )
    fit, hist = fit_em(init, batch, n_iter=60)
    true_ll = forward(P.derived(), batch).loglik.sum().item()
    assert hist[-1] >= true_ll - 5  # EM reaches (at least) the truth's likelihood
    assert mx.abs(fit.B - P.B).max().item() < 0.08
    assert mx.abs(fit.C - P.C).max().item() < 0.1
    assert mx.abs(fit.A - P.A).max().item() < 0.25
    assert mx.all(mx.abs(fit.mu - P.mu) / P.mu < 0.25).item()
