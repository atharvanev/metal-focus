import math

import mlx.core as mx
import pytest

from hsmm.params import HSMMParams, fit_truncated_nb, hazard_from_pmf, nb_log_pmf


def _nb_reference(mu, r, D):
    m = mu - 1.0
    raw = [
        math.exp(math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1) + r * math.log(r / (r + m)) + k * math.log(m / (r + m)))
        for k in range(D)
    ]
    s = sum(raw)
    return [x / s for x in raw]


@pytest.mark.parametrize("mu,r,D", [(3.0, 4.0, 30), (15.0, 2.0, 30), (1.5, 0.5, 10), (8.0, 50.0, 30)])
def test_nb_pmf_matches_lgamma_reference(mu, r, D):
    got = mx.exp(nb_log_pmf(mx.array(mu), mx.array(r), D)).tolist()
    ref = _nb_reference(mu, r, D)
    assert sum(got) == pytest.approx(1.0, abs=1e-5)
    assert got == pytest.approx(ref, abs=1e-5)


def test_mean_close_to_mu_when_truncation_negligible():
    p = mx.exp(nb_log_pmf(mx.array(4.0), mx.array(5.0), 30))
    mean = (p * mx.arange(1, 31)).sum().item()
    assert mean == pytest.approx(4.0, abs=1e-3)


def test_hazard_properties():
    p = mx.exp(nb_log_pmf(mx.array([3.0, 15.0]), mx.array([4.0, 2.0]), 30))
    h = hazard_from_pmf(p)
    assert mx.all(h >= 0).item() and mx.all(h <= 1).item()
    assert h[:, -1].tolist() == [1.0, 1.0]
    # reconstruct pmf from hazards: p(d) = h(d) prod_{k<d} (1 - h(k))
    surv = mx.concatenate([mx.ones((2, 1)), mx.cumprod(1 - h[:, :-1], axis=1)], axis=1)
    assert mx.allclose(surv * h, p, atol=1e-6).item()


def test_create_enforces_constraints():
    P = HSMMParams.create(
        pi=[1, 1, 1, 1], A=mx.ones((4, 4)), B=[[1, 0, 0, 0, 0]] * 4, C=[[1, 1, 1]] * 4, mu=[2, 3, 4, 50], r=[1, 1, 1, 1]
    )
    assert mx.all(mx.diag(P.A) == 0).item()
    assert mx.allclose(P.A.sum(1), mx.ones(4)).item()
    assert mx.all(P.B > 0).item()  # floored
    assert P.mu[-1].item() == 30.0  # clipped to D


def test_roundtrip_dict(tmp_path):
    P = HSMMParams.create(pi=[1, 2], A=[[0, 1], [1, 0]], B=[[1, 2], [2, 1]], C=[[1], [1]], mu=[2, 3], r=[1, 2], D=5)
    path = tmp_path / "p.json"
    P.save(path)
    Q = HSMMParams.load(path)
    assert mx.allclose(P.B, Q.B).item() and Q.D == 5


def test_fit_truncated_nb_recovers_from_exact_pmf():
    true_mu, true_r = mx.array([3.0, 15.0, 6.0]), mx.array([4.0, 2.0, 10.0])
    counts = 1000 * mx.exp(nb_log_pmf(true_mu, true_r, 30))
    mu, r = fit_truncated_nb(counts, 30)
    assert mu.tolist() == pytest.approx(true_mu.tolist(), rel=0.06)
    # r is weakly identified; check the fitted pmf instead of r itself
    fitted = mx.exp(nb_log_pmf(mu, r, 30))
    assert mx.abs(fitted - counts / 1000).max().item() < 0.01
