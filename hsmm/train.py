"""Nightly evidence-weighted update (and plain EM for validation).

update rule, with today's tables acting as a prior of fixed weight:
  B_new[j]  = (w_B * B[j] + soft counts) / (w_B + time in state j)     (same for C)
  A_new[j]  = (w_A * A[j] + expected switches out of j) / (w_A + total)
  dur_j     = refit truncated NB to  w_D * p_j(d) + n_j(d)
  pi_new    = (w_pi * pi + expected starts) / (w_pi + number of starts)
A state whose (weighted) occupancy is below `eps` keeps its rows unchanged.
With all prior weights 0 this is one Baum-Welch (EM) iteration.

Clamping/weighting comes from the Batch: check-in windows hard (1/0) and
weighted 3x, auto labels soft (0.9/0.1). Eval-split check-ins must never be
in the Batch (the labels/merge output already excludes them).
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import mlx.core as mx

from common.device import train_stream
from hsmm.backward import smooth
from hsmm.batch import Batch
from hsmm.config import load_config
from hsmm.params import HSMMParams, fit_truncated_nb


@dataclass
class Counts:
    B: mx.array  # K x n_cat
    occ_B: mx.array  # K
    C: mx.array  # K x n_bin
    occ_C: mx.array  # K
    trans: mx.array  # K x K
    dur: mx.array  # K x D
    pi: mx.array  # K
    n_starts: float
    occ: mx.array  # K (weighted time in state, all active windows)
    loglik: float


def _onehot(idx: mx.array, n: int) -> mx.array:
    """... -> ... x n; index -1 (missing) -> all zeros."""
    return (idx[..., None] == mx.arange(n)).astype(mx.float32)


def expected_counts(params: HSMMParams, batch: Batch) -> Counts:
    """E-step. A sequence whose likelihood underflowed (labels/params made it
    impossible) is dropped from every count with a warning, never NaN."""
    der = params.derived()
    post = smooth(der, batch)
    K = params.K
    ok = ~post.forward.underflow  # N
    n_bad = int((~ok).sum().item())
    if n_bad:
        warnings.warn(f"HSMM: {n_bad} sequence(s) had ~zero likelihood and were skipped", RuntimeWarning, stacklevel=2)
    okf = ok.astype(mx.float32)
    w = (batch.active.astype(mx.float32) * batch.weight * okf[:, None])[..., None]
    gamma = mx.where(ok[:, None, None], post.gamma, 0.0)
    trans = mx.where(ok[:, None, None], post.trans, 0.0)
    dur = mx.where(ok[:, None, None], post.dur, 0.0)
    g = (gamma * w).reshape(-1, K)  # NT x K
    oc = _onehot(batch.cat, params.B.shape[1]).reshape(g.shape[0], -1)
    os = _onehot(batch.sw, params.C.shape[1]).reshape(g.shape[0], -1)
    cB, cC = g.T @ oc, g.T @ os
    starts = (batch.restart.astype(mx.float32) * okf[:, None])[..., None]
    pi_c = (gamma * starts).sum(axis=(0, 1))
    out = Counts(
        B=cB,
        occ_B=cB.sum(axis=1),
        C=cC,
        occ_C=cC.sum(axis=1),
        trans=trans.sum(axis=0),
        dur=dur.sum(axis=0),
        pi=pi_c,
        n_starts=float(starts.sum().item()),
        occ=g.sum(axis=0),
        loglik=float(mx.where(ok, post.loglik, 0.0).sum().item()),
    )
    mx.eval(out.B, out.C, out.trans, out.dur, out.pi, out.occ)
    return out


def apply_update(params: HSMMParams, c: Counts, prior: dict, eps: float = 1e-3, floor: float = 1e-4) -> HSMMParams:
    wB, wC, wA, wD, wpi = (float(prior[k]) for k in ("B", "C", "A", "duration", "pi"))

    def rows(old, counts, occ, w):
        keep = (occ < eps)[:, None]
        new = (w * old + counts) / mx.maximum(w + occ, 1e-12)[:, None]
        return mx.where(keep, old, new)

    B = rows(params.B, c.B, c.occ_B, wB)
    C = rows(params.C, c.C, c.occ_C, wC)
    A = rows(params.A, c.trans, c.trans.sum(axis=1), wA)

    n_end = c.dur.sum(axis=1)
    hist = wD * params.duration_pmf() + c.dur
    mu_fit, r_fit = fit_truncated_nb(hist, params.D)
    keep = n_end < eps
    mu = mx.where(keep, params.mu, mu_fit)
    r = mx.where(keep, params.r, r_fit)

    pi = params.pi if c.n_starts < eps else (wpi * params.pi + c.pi) / (wpi + c.n_starts)
    return HSMMParams.create(pi, A, B, C, mu, r, D=params.D, floor=floor)


def nightly_update(params: HSMMParams, batch: Batch, cfg: dict | None = None) -> tuple[HSMMParams, Counts]:
    """One evidence-weighted update on the last 2-4 weeks of days (as a Batch)."""
    cfg = cfg or load_config()
    with train_stream():
        c = expected_counts(params, batch)
        new = apply_update(params, c, cfg["prior"], eps=cfg["min_occupancy"], floor=cfg["emission_floor"])
        return new, c


ZERO_PRIOR = {"B": 0.0, "C": 0.0, "A": 0.0, "duration": 0.0, "pi": 0.0}


def fit_em(params: HSMMParams, batch: Batch, n_iter: int = 50, tol: float = 1e-3, prior: dict | None = None):
    """Plain EM (zero prior by default). Returns (params, loglik history)."""
    hist: list[float] = []
    with train_stream():
        for _ in range(n_iter):
            c = expected_counts(params, batch)
            hist.append(c.loglik)
            params = apply_update(params, c, prior or ZERO_PRIOR)
            if len(hist) > 1 and abs(hist[-1] - hist[-2]) < tol:
                break
    return params, hist
