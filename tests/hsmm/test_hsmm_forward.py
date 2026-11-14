import math

import mlx.core as mx
import pytest
from hsmm_bruteforce import brute

from hsmm.batch import DayObs, build_batch
from hsmm.forward import forecast, forward, predict, state_probs


def _batch(cats, sws, away=None, K=3, **kw):
    return build_batch([DayObs(cat=cats, sw=sws, away=away)], K=K, **kw)


def test_loglik_and_filter_match_bruteforce(tiny_params, tiny_obs):
    der = tiny_params.derived()
    ref = brute(tiny_params, tiny_obs["cats"], tiny_obs["sws"])
    fr = forward(der, _batch(tiny_obs["cats"], tiny_obs["sws"]))
    assert fr.loglik.item() == pytest.approx(math.log(ref.Z), abs=1e-4)
    assert state_probs(fr.alpha[-1, 0]).tolist() == pytest.approx(ref.filt_last, abs=1e-5)


def test_every_prefix_matches_bruteforce(tiny_params, tiny_obs):
    der = tiny_params.derived()
    fr = forward(der, _batch(tiny_obs["cats"], tiny_obs["sws"]))
    cum = mx.cumsum(fr.logc[0]).tolist()
    for t in range(1, len(tiny_obs["cats"]) + 1):
        ref = brute(tiny_params, tiny_obs["cats"][:t], tiny_obs["sws"][:t])
        assert cum[t - 1] == pytest.approx(math.log(ref.Z), abs=1e-4)


def test_away_windows_freeze_belief(tiny_params, tiny_obs):
    """Inserting Away windows (short of a break) is identical to removing them."""
    der = tiny_params.derived()
    c, s = tiny_obs["cats"], tiny_obs["sws"]
    c2 = c[:3] + [2, 2, 2] + c[3:]
    s2 = s[:3] + [1, 1, 1] + s[3:]
    away = [False] * 3 + [True] * 3 + [False] * (len(c) - 3)
    a = forward(der, _batch(c, s))
    b = forward(der, _batch(c2, s2, away=away))
    assert b.loglik.item() == pytest.approx(a.loglik.item(), abs=1e-5)
    assert mx.allclose(b.alpha[-1], a.alpha[-1], atol=1e-6).item()
    # belief during the away run equals the belief right before it
    assert mx.allclose(b.alpha[5], b.alpha[2]).item()


def test_long_away_is_sequence_break(tiny_params, tiny_obs):
    der = tiny_params.derived()
    c, s = tiny_obs["cats"], tiny_obs["sws"]
    away = [False] * 3 + [True] * 2 + [False] * 2
    fr = forward(der, _batch(c, s, away=away, away_break_windows=2))
    first = brute(tiny_params, c[:3], s[:3])
    second = brute(tiny_params, c[5:], s[5:])
    assert fr.loglik.item() == pytest.approx(math.log(first.Z) + math.log(second.Z), abs=1e-4)


def test_batched_padding_matches_individual(tiny_params, tiny_obs):
    der = tiny_params.derived()
    c, s = tiny_obs["cats"], tiny_obs["sws"]
    days = [DayObs(cat=c, sw=s), DayObs(cat=c[:4], sw=s[:4])]
    fr = forward(der, build_batch(days, K=3))
    assert fr.loglik[1].item() == pytest.approx(math.log(brute(tiny_params, c[:4], s[:4]).Z), abs=1e-4)
    assert fr.loglik[0].item() == pytest.approx(math.log(brute(tiny_params, c, s).Z), abs=1e-4)


def test_predict_preserves_mass_and_forecast_converges(tiny_params, tiny_obs):
    der = tiny_params.derived()
    fr = forward(der, _batch(tiny_obs["cats"], tiny_obs["sws"]))
    alpha = fr.alpha[-1]
    assert predict(der, alpha).sum().item() == pytest.approx(1.0, abs=1e-5)
    f1 = forecast(der, alpha, 1)
    assert f1.sum().item() == pytest.approx(1.0, abs=1e-5)
