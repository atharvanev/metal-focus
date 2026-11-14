import itertools
import math

import mlx.core as mx
import pytest

from baselines.hmm import HMMParams, LiveHMM, fit_em, forward, smooth
from common.model_api import LiveModel
from hsmm.batch import DayObs, build_batch
from hsmm.simulate import default_true_params, sample_days


@pytest.fixture
def tiny():
    return HMMParams.create(
        pi=[0.6, 0.4],
        A=[[0.8, 0.2], [0.3, 0.7]],
        B=[[0.7, 0.2, 0.1], [0.1, 0.3, 0.6]],
        C=[[0.8, 0.2], [0.3, 0.7]],
        floor=0.0,
    )


def _brute(P, cats, sws):
    pi, A, B, C = (getattr(P, k).tolist() for k in ("pi", "A", "B", "C"))
    T, K = len(cats), len(pi)
    Z, smooth_ = 0.0, [[0.0] * K for _ in range(T)]
    for s in itertools.product(range(K), repeat=T):
        p = pi[s[0]]
        for t in range(1, T):
            p *= A[s[t - 1]][s[t]]
        for t in range(T):
            p *= (B[s[t]][cats[t]] if cats[t] >= 0 else 1) * (C[s[t]][sws[t]] if sws[t] >= 0 else 1)
        Z += p
        for t in range(T):
            smooth_[t][s[t]] += p
    return Z, [[x / Z for x in r] for r in smooth_]


def test_hmm_matches_bruteforce(tiny):
    cats, sws = [0, 2, -1, 1, 2, 0], [0, 1, 1, -1, 1, 0]
    b = build_batch([DayObs(cat=cats, sw=sws)], K=2)
    Z, sm = _brute(tiny, cats, sws)
    _, logc, _ = forward(tiny, b)
    assert logc.sum().item() == pytest.approx(math.log(Z), abs=1e-4)
    post = smooth(tiny, b)
    assert post.gamma[0].tolist() == [pytest.approx(r, abs=1e-5) for r in sm]
    # expected switch counts sum to T-1 transitions
    assert post.xi.sum().item() == pytest.approx(len(cats) - 1, abs=1e-4)


def test_hmm_away_freezes(tiny):
    cats, sws = [0, 2, 1], [0, 1, 0]
    a = forward(tiny, build_batch([DayObs(cat=cats, sw=sws)], K=2))[1].sum().item()
    b = forward(tiny, build_batch([DayObs(cat=[0, 2, 2, 1], sw=[0, 1, 1, 0], away=[False, False, True, False])], K=2))[1].sum().item()
    assert a == pytest.approx(b, abs=1e-6)


def test_hmm_em_increases_loglik_and_live_matches():
    P = HMMParams.from_hsmm(default_true_params())
    days = sample_days(default_true_params(), 4, 120, seed=5)
    b = build_batch(days, K=4)
    fit, hist = fit_em(P, b, n_iter=6, tol=0.0)
    assert all(y >= x - 1e-2 for x, y in zip(hist, hist[1:]))
    m = LiveHMM(fit)
    assert isinstance(m, LiveModel)
    outs = [m.step_obs(c, s) for c, s in zip(days[0].cat, days[0].sw)]
    alpha = forward(fit, build_batch([days[0]], K=4))[0][:, 0]
    assert mx.allclose(mx.array([list(o.p_state) for o in outs]), alpha, atol=1e-5).item()


def test_hsmm_beats_hmm_on_hsmm_data():
    """Sanity: on data with non-geometric durations, the HSMM should win held-out log-lik."""
    from hsmm.forward import forward as hsmm_forward

    true = default_true_params()
    test = build_batch(sample_days(true, 8, 240, seed=21), K=4)
    hsmm_ll = hsmm_forward(true.derived(), test).loglik.sum().item()
    train = build_batch(sample_days(true, 12, 240, seed=22), K=4)
    hmm, _ = fit_em(HMMParams.from_hsmm(true), train, n_iter=30)
    hmm_ll = forward(hmm, test)[1].sum().item()
    assert hsmm_ll > hmm_ll
