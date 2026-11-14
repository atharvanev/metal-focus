"""HMM baseline: same states and emissions as the HSMM, geometric durations.

Exists to justify the HSMM's duration model (CLAUDE.md "Model lineup" #2).
Uses the same Batch, evidence factors, Away freezing and restart semantics as
the HSMM, so held-out log-likelihoods are directly comparable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import mlx.core as mx

from common.device import live_stream, train_stream
from common.model_api import Readout
from hsmm.batch import Batch
from hsmm.config import load_config
from hsmm.forward import RISK_STATES
from hsmm.io import day_to_obs, days_to_batch, window_to_obs
from hsmm.params import HSMMParams, floor_rows


@dataclass(frozen=True)
class HMMParams:
    pi: mx.array  # K
    A: mx.array  # K x K, diagonal = stay probability
    B: mx.array  # K x n_cat
    C: mx.array  # K x n_bin

    @property
    def K(self) -> int:
        return self.pi.shape[0]

    @staticmethod
    def create(pi, A, B, C, floor: float = 1e-4) -> "HMMParams":
        f = lambda x: mx.array(x, dtype=mx.float32)  # noqa: E731
        return HMMParams(floor_rows(f(pi), floor), floor_rows(f(A), floor), floor_rows(f(B), floor), floor_rows(f(C), floor))

    @staticmethod
    def from_hsmm(p: HSMMParams) -> "HMMParams":
        """Geometric approximation: stay with prob 1 - 1/mu, else switch per A."""
        stay = 1.0 - 1.0 / p.mu
        A = mx.diag(stay) + (1.0 - stay)[:, None] * p.A
        return HMMParams.create(p.pi, A, p.B, p.C)

    def to_dict(self) -> dict:
        return {k: getattr(self, k).tolist() for k in ("pi", "A", "B", "C")}


def evidence(P: HMMParams, cat, sw, label_factor=None) -> mx.array:
    ones = mx.ones((P.K, 1))
    Bx, Cx = mx.concatenate([P.B, ones], 1), mx.concatenate([P.C, ones], 1)
    ci = mx.where(cat < 0, P.B.shape[1], cat)
    si = mx.where(sw < 0, P.C.shape[1], sw)
    e = mx.take(Bx.T, ci, axis=0) * mx.take(Cx.T, si, axis=0)
    return e * label_factor if label_factor is not None else e


def forward_step(P: HMMParams, alpha, e_t, active_t, restart_t):
    N = alpha.shape[0]
    pred = mx.where(restart_t[:, None], mx.broadcast_to(P.pi[None], alpha.shape), alpha @ P.A)
    a = pred * e_t
    c = a.sum(axis=1)
    c_safe = mx.where(active_t, mx.maximum(c, 1e-38), mx.ones((N,)))
    a = a / c_safe[:, None]
    return mx.where(active_t[:, None], a, alpha), mx.where(active_t, mx.log(c_safe), mx.zeros((N,)))


@dataclass
class HMMPosterior:
    alpha: mx.array  # T x N x K
    gamma: mx.array  # N x T x K
    xi: mx.array  # N x K x K (weighted)
    logc: mx.array  # N x T

    @property
    def loglik(self) -> mx.array:
        return self.logc.sum(axis=1)


def forward(P: HMMParams, batch: Batch, use_labels: bool = True):
    e = evidence(P, batch.cat, batch.sw, batch.label_factor if use_labels else None)
    alpha = mx.zeros((batch.N, P.K))
    alphas, logcs = [], []
    for t in range(batch.T):
        alpha, lc = forward_step(P, alpha, e[:, t], batch.active[:, t], batch.restart[:, t])
        mx.eval(alpha, lc)
        alphas.append(alpha)
        logcs.append(lc)
    return mx.stack(alphas), mx.stack(logcs, axis=1), e


def smooth(P: HMMParams, batch: Batch, use_labels: bool = True) -> HMMPosterior:
    alphas, logc, e = forward(P, batch, use_labels)
    c = mx.exp(logc)
    N, T, K = batch.N, batch.T, P.K
    beta = mx.ones((N, K))
    gammas = [None] * T
    gammas[T - 1] = alphas[T - 1] * beta
    xi = mx.zeros((N, K, K))
    cont = batch.active & ~batch.restart
    for t in range(T - 2, -1, -1):
        e1, c1 = e[:, t + 1], c[:, t + 1][:, None]
        into = e1 * beta  # N x K
        b_cont = (into @ P.A.T) / c1
        b_rs = (P.pi * into).sum(axis=1, keepdims=True) / c1 * mx.ones((1, K))
        a1, r1 = batch.active[:, t + 1][:, None], batch.restart[:, t + 1][:, None]
        new_beta = mx.where(a1, mx.where(r1, b_rs, b_cont), beta)
        m = (cont[:, t + 1].astype(mx.float32) * batch.weight_eff[:, t])[:, None, None]
        xi = xi + m * alphas[t][:, :, None] * P.A[None] * into[:, None, :] / c1[:, :, None]
        beta = new_beta
        gammas[t] = alphas[t] * beta
        mx.eval(beta, xi, gammas[t])
    return HMMPosterior(alpha=alphas, gamma=mx.stack(gammas, axis=1), xi=xi, logc=logc)


def em_step(P: HMMParams, batch: Batch, prior: float = 0.0, eps: float = 1e-3) -> tuple[HMMParams, float]:
    post = smooth(P, batch)
    K = P.K
    w = (batch.active.astype(mx.float32) * batch.weight)[..., None]
    g = (post.gamma * w).reshape(-1, K)
    oc = (batch.cat[..., None] == mx.arange(P.B.shape[1])).astype(mx.float32).reshape(g.shape[0], -1)
    os = (batch.sw[..., None] == mx.arange(P.C.shape[1])).astype(mx.float32).reshape(g.shape[0], -1)

    def rows(old, counts):
        occ = counts.sum(axis=1)
        new = (prior * old + counts) / mx.maximum(prior + occ, 1e-12)[:, None]
        return mx.where((occ < eps)[:, None], old, new)

    starts = batch.restart.astype(mx.float32)[..., None]
    pi_c = (post.gamma * starts).sum(axis=(0, 1))
    n = float(starts.sum().item())
    pi = P.pi if n < eps else (prior * P.pi + pi_c) / (prior + n)
    new = HMMParams.create(pi, rows(P.A, post.xi.sum(0)), rows(P.B, g.T @ oc), rows(P.C, g.T @ os))
    return new, float(post.loglik.sum().item())


def fit_em(P: HMMParams, batch: Batch, n_iter: int = 50, tol: float = 1e-3, prior: float = 0.0):
    hist: list[float] = []
    with train_stream():
        for _ in range(n_iter):
            P, ll = em_step(P, batch, prior)
            hist.append(ll)
            if len(hist) > 1 and abs(hist[-1] - hist[-2]) < tol:
                break
    return P, hist


class LiveHMM:
    """Implements common.model_api.LiveModel / TrainableModel."""

    def __init__(self, params: HMMParams, cfg: dict | None = None):
        self.cfg = cfg or load_config()
        self.params = params
        self.reset()

    def reset(self) -> None:
        self.alpha = mx.zeros((1, self.params.K))
        self.started, self.away_run = False, 0
        self.last: Readout | None = None

    def step(self, window: Any) -> Readout:
        return self.step_obs(*window_to_obs(window, self.cfg))

    def step_obs(self, cat: int, sw: int, away: bool = False) -> Readout:
        if away:
            self.away_run += 1
            base = self.last or Readout(risk=0.0)
            return Readout(**{**base.__dict__, "away": True})
        restart = (not self.started) or self.away_run >= self.cfg["away_break_windows"]
        self.started, self.away_run = True, 0
        with live_stream():
            e = evidence(self.params, mx.array([[cat]]), mx.array([[sw]]))[:, 0]
            self.alpha, logc = forward_step(self.params, self.alpha, e, mx.array([True]), mx.array([restart]))
            fc = {}
            a = self.alpha
            for k in range(1, max(self.cfg["forecast_windows"]) + 1):
                a = a @ self.params.A
                if k in self.cfg["forecast_windows"]:
                    fc[k] = a
            mx.eval(self.alpha, logc, *fc.values())
        ps = self.alpha[0].tolist()
        top = max(range(len(ps)), key=ps.__getitem__)
        stay = self.params.A[top, top].item()
        self.last = Readout(
            risk=sum(ps[j] for j in RISK_STATES),
            p_state=tuple(ps),
            top_state=top,
            top_prob=ps[top],
            hazard=1.0 - stay,  # geometric: constant hazard
            forecast_risk={k: sum(v[0, j].item() for j in RISK_STATES) for k, v in fc.items()},
            surprise=-logc.item(),
        )
        return self.last

    def fit(self, days: Sequence[Sequence[Any]], labels: Sequence[Any] | None = None) -> None:
        obs = [day_to_obs(d, labels[i] if labels else None, self.cfg) for i, d in enumerate(days)]
        prior = float(load_config()["prior"]["B"])
        self.params, _ = fit_em(self.params, days_to_batch(obs, self.cfg), n_iter=1, prior=prior)
