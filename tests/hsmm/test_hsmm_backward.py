import mlx.core as mx
import pytest
from hsmm_bruteforce import brute

from hsmm.backward import smooth, viterbi
from hsmm.batch import HARD, SOFT, DayObs, build_batch


def _batch(cats, sws, **kw):
    return build_batch([DayObs(cat=cats, sw=sws, **kw)], K=3)


def test_gamma_sums_to_one_and_matches_bruteforce(tiny_params, tiny_obs):
    der = tiny_params.derived()
    post = smooth(der, _batch(tiny_obs["cats"], tiny_obs["sws"]))
    ref = brute(tiny_params, tiny_obs["cats"], tiny_obs["sws"])
    g = post.gamma[0]
    assert mx.allclose(g.sum(axis=1), mx.ones(g.shape[0]), atol=1e-5).item()
    assert g.tolist() == [pytest.approx(row, abs=1e-5) for row in ref.smooth]


def test_expected_counts_match_bruteforce(tiny_params, tiny_obs):
    der = tiny_params.derived()
    post = smooth(der, _batch(tiny_obs["cats"], tiny_obs["sws"]))
    ref = brute(tiny_params, tiny_obs["cats"], tiny_obs["sws"])
    assert post.trans[0].tolist() == [pytest.approx(row, abs=1e-5) for row in ref.trans]
    assert post.dur[0].tolist() == [pytest.approx(row, abs=1e-5) for row in ref.dur]
    # sum_d n_j(d) == number of switches out of j
    assert mx.allclose(post.dur[0].sum(axis=1), post.trans[0].sum(axis=1), atol=1e-6).item()


def test_clamped_posteriors_match_bruteforce(tiny_params, tiny_obs):
    der = tiny_params.derived()
    n = len(tiny_obs["cats"])
    label = [-1, 2, -1, -1, 0, -1, -1]
    clamp = [None, HARD, None, None, SOFT, None, None]
    b = _batch(tiny_obs["cats"], tiny_obs["sws"], label=label, clamp=clamp)
    post = smooth(der, b)
    ref = brute(tiny_params, tiny_obs["cats"], tiny_obs["sws"], label_factor=b.label_factor[0].tolist())
    assert post.gamma[0].tolist() == [pytest.approx(row, abs=1e-5) for row in ref.smooth]
    assert post.gamma[0, 1, 2].item() == pytest.approx(1.0, abs=1e-4)  # hard_off = 1e-6, not 0
    assert len(ref.smooth) == n


def test_away_run_contributes_nothing(tiny_params, tiny_obs):
    der = tiny_params.derived()
    c, s = tiny_obs["cats"], tiny_obs["sws"]
    a = smooth(der, _batch(c, s))
    b = smooth(der, _batch(c[:3] + [0, 0] + c[3:], s[:3] + [0, 0] + s[3:], away=[False] * 3 + [True] * 2 + [False] * 4))
    assert mx.allclose(a.trans, b.trans, atol=1e-6).item()
    assert mx.allclose(a.dur, b.dur, atol=1e-6).item()


def test_viterbi_matches_bruteforce(tiny_params, tiny_obs):
    der = tiny_params.derived()
    ref = brute(tiny_params, tiny_obs["cats"], tiny_obs["sws"])
    assert tuple(viterbi(der, _batch(tiny_obs["cats"], tiny_obs["sws"]))) == ref.best


def test_viterbi_marks_away_and_splits(tiny_params, tiny_obs):
    der = tiny_params.derived()
    c, s = tiny_obs["cats"], tiny_obs["sws"]
    b = build_batch([DayObs(cat=c, sw=s, away=[False, False, True, True, False, False, False])], K=3, away_break_windows=2)
    path = viterbi(der, b)
    assert path[2:4] == [-1, -1]
    assert tuple(path[:2]) == brute(tiny_params, c[:2], s[:2]).best
    assert tuple(path[4:]) == brute(tiny_params, c[4:], s[4:]).best
