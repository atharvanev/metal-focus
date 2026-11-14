"""Forward pass (filtering) in the elapsed-duration formulation.

alpha_t(j, d) = P(state j, d windows elapsed in it (incl. t) | obs up to t),
normalized every step; log c_t is the per-window log-likelihood (surprise is
-log c_t). All functions are batched over a leading N axis; live inference
uses N = 1.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import mlx.core as mx

from hsmm.batch import Batch
from hsmm.params import Derived

RISK_STATES = (2, 3)  # Drifting, Rabbit hole


def pi_table(der: Derived) -> mx.array:
    """K x D table with pi in the d=1 column."""
    P = der.params
    return mx.concatenate([P.pi[:, None], mx.zeros((P.K, P.D - 1))], axis=1)


def predict(der: Derived, alpha: mx.array) -> mx.array:
    """Step 1 (time passes), N x K x D -> N x K x D. Preserves total mass."""
    h, A = der.h, der.params.A
    N, K, D = alpha.shape
    cont = alpha * (1.0 - h)
    shifted = mx.concatenate([mx.zeros((N, K, 1)), cont[:, :, :-1]], axis=2)
    entering = (alpha * h).sum(axis=2) @ A  # N x K
    return shifted + mx.concatenate([entering[:, :, None], mx.zeros((N, K, D - 1))], axis=2)


def evidence(der: Derived, cat: mx.array, sw: mx.array, label_factor: mx.array | None = None) -> mx.array:
    """Step 2 factors e_t(j) = B_j(o_t) C_j(bin_t) [x clamp], shape cat.shape + (K,).

    Missing observations (index -1) map to the all-ones column (factor 1).
    """
    n_cat = der.params.B.shape[1]
    n_bin = der.params.C.shape[1]
    ci = mx.where(cat < 0, n_cat, cat)
    si = mx.where(sw < 0, n_bin, sw)
    e = mx.take(der.Bx.T, ci, axis=0) * mx.take(der.Cx.T, si, axis=0)
    if label_factor is not None:
        e = e * label_factor
    return e


def forward_step(der: Derived, alpha, e_t, active_t, restart_t):
    """One filtering step for a batch. Returns (alpha_new, log c_t).

    Inactive windows (Away / padding) leave alpha unchanged with log c = 0.
    """
    N = alpha.shape[0]
    pi_tab = mx.broadcast_to(pi_table(der)[None], alpha.shape)
    pred = mx.where(restart_t[:, None, None], pi_tab, predict(der, alpha))
    a = pred * e_t[:, :, None]
    c = a.sum(axis=(1, 2))
    c_safe = mx.where(active_t, mx.maximum(c, 1e-38), mx.ones((N,)))
    a = a / c_safe[:, None, None]
    alpha_new = mx.where(active_t[:, None, None], a, alpha)
    logc = mx.where(active_t, mx.log(c_safe), mx.zeros((N,)))
    return alpha_new, logc


@dataclass
class ForwardResult:
    alpha: mx.array  # T x N x K x D (filtered, normalized)
    logc: mx.array  # N x T
    e: mx.array  # N x T x K evidence factors used
    underflow: mx.array  # N bool: some active window had c_t ~ 0 (labels/params made it impossible)

    @property
    def loglik(self) -> mx.array:  # N
        return self.logc.sum(axis=1)


def forward(der: Derived, batch: Batch, use_labels: bool = True, underflow_c: float = 1e-30) -> ForwardResult:
    e = evidence(der, batch.cat, batch.sw, batch.label_factor if use_labels else None)
    N, T = batch.N, batch.T
    alpha = mx.zeros((N, der.params.K, der.params.D))
    alphas, logcs = [], []
    for t in range(T):
        alpha, lc = forward_step(der, alpha, e[:, t], batch.active[:, t], batch.restart[:, t])
        mx.eval(alpha, lc)
        alphas.append(alpha)
        logcs.append(lc)
    logc = mx.stack(logcs, axis=1)
    bad = (batch.active & (logc < math.log(underflow_c))).any(axis=1)
    return ForwardResult(alpha=mx.stack(alphas), logc=logc, e=e, underflow=bad)


# ---------------------------------------------------------------- readouts


def state_probs(alpha: mx.array) -> mx.array:
    return alpha.sum(axis=-1)


def expected_elapsed(alpha: mx.array) -> mx.array:
    """E[d | state j] in windows, shape ... x K."""
    D = alpha.shape[-1]
    d = mx.arange(1, D + 1, dtype=mx.float32)
    p = alpha.sum(axis=-1)
    return (alpha * d).sum(axis=-1) / mx.maximum(p, 1e-12)


def expected_hazard(der: Derived, alpha: mx.array) -> mx.array:
    """P(current stretch ends after this window | state j), shape ... x K."""
    p = alpha.sum(axis=-1)
    return (alpha * der.h).sum(axis=-1) / mx.maximum(p, 1e-12)


def risk(alpha: mx.array) -> mx.array:
    sp = state_probs(alpha)
    return sum(sp[..., j] for j in RISK_STATES)


def forecast(der: Derived, alpha: mx.array, k: int) -> mx.array:
    """State probabilities k windows ahead (step 1 only, no evidence)."""
    for _ in range(k):
        alpha = predict(der, alpha)
    return state_probs(alpha)
