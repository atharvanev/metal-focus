"""Backward pass, smoothed posteriors, expected counts, and Viterbi.

Scaled beta, using the forward normalizers c_t, so that
sum_{j,d} alpha_t(j,d) beta_t(j,d) = 1 for every t:

  beta_t(j,d) = [ (1-h_j(d)) e_{t+1}(j) beta_{t+1}(j,d+1)
                  + h_j(d) sum_k A_jk e_{t+1}(k) beta_{t+1}(k,1) ] / c_{t+1}

At an inactive window t+1 (Away/padding) beta_t = beta_{t+1}; at a restart
window it is the constant sum_k pi_k e_{t+1}(k) beta_{t+1}(k,1) / c_{t+1}.

Expected counts come only from stretch *ends* followed by an active,
non-restart window, so the stretch still running at end of day (or at a long
Away break) is censored: it contributes no duration or switch count.
"""

from __future__ import annotations

from dataclasses import dataclass

import mlx.core as mx

from hsmm.batch import Batch
from hsmm.forward import ForwardResult, forward, pi_table, predict  # noqa: F401
from hsmm.params import Derived


@dataclass
class Posterior:
    gamma: mx.array  # N x T x K  P(state at t | all obs) (inactive t: copy of last active)
    trans: mx.array  # N x K x K  expected switches j -> k (weighted by weight_eff)
    dur: mx.array  # N x K x D  expected completed-stretch lengths n_j(d) (weighted)
    loglik: mx.array  # N
    forward: ForwardResult


def smooth(der: Derived, batch: Batch, use_labels: bool = True) -> Posterior:
    fr = forward(der, batch, use_labels=use_labels)
    P = der.params
    h, A = der.h, P.A
    N, T, K, D = batch.N, batch.T, P.K, P.D
    c = mx.exp(fr.logc)  # inactive windows -> 1
    act = batch.active
    cont_ok = act & ~batch.restart  # windows reached by a real step 1

    beta = mx.ones((N, K, D))
    gammas = [None] * T
    gammas[T - 1] = (fr.alpha[T - 1] * beta).sum(axis=2)
    trans = mx.zeros((N, K, K))
    dur = mx.zeros((N, K, D))
    zcol = mx.zeros((N, K, 1))
    for t in range(T - 2, -1, -1):
        e1 = fr.e[:, t + 1]  # N x K
        c1 = c[:, t + 1][:, None, None]
        into1 = e1 * beta[:, :, 0]  # N x K
        trans_term = into1 @ A.T  # N x K: sum_k A_jk e(k) beta(k,1)
        shifted = mx.concatenate([beta[:, :, 1:], zcol], axis=2)
        b_cont = ((1.0 - h) * e1[:, :, None] * shifted + h * trans_term[:, :, None]) / c1
        b_rs = ((P.pi * into1).sum(axis=1)[:, None, None] / c1) * mx.ones((1, K, D))
        a1, r1 = act[:, t + 1][:, None, None], batch.restart[:, t + 1][:, None, None]
        new_beta = mx.where(a1, mx.where(r1, b_rs, b_cont), beta)

        # stretch ending at t, next stretch starting at t+1
        alpha_t = fr.alpha[t]
        mask = (cont_ok[:, t + 1].astype(mx.float32) * batch.weight_eff[:, t])[:, None, None]
        endmass = alpha_t * h  # N x K x D
        trans = trans + mask * endmass.sum(axis=2)[:, :, None] * A[None] * into1[:, None, :] / c1
        dur = dur + mask * endmass * trans_term[:, :, None] / c1

        beta = new_beta
        gammas[t] = (alpha_t * beta).sum(axis=2)
        mx.eval(beta, trans, dur, gammas[t])
    return Posterior(gamma=mx.stack(gammas, axis=1), trans=trans, dur=dur, loglik=fr.loglik, forward=fr)


# ---------------------------------------------------------------- Viterbi


def viterbi_segment(der: Derived, e: mx.array) -> list[int]:
    """Most likely state path for one contiguous active segment.

    e: T x K evidence factors (already including any clamps). Same lattice
    as the forward pass with max instead of sum; censored ending.
    """
    P = der.params
    T, K, D = e.shape[0], P.K, P.D
    with_errstate = lambda x: mx.log(mx.maximum(x, 0.0))  # noqa: E731  log(0) = -inf
    log_h, log_1mh, log_A = with_errstate(der.h), with_errstate(1.0 - der.h), with_errstate(P.A)
    log_e = with_errstate(e)
    delta = with_errstate(pi_table(der)) + log_e[0][:, None]
    neg_inf = mx.full((K, 1), -mx.inf)
    back_d, back_j = [], []
    for t in range(1, T):
        ends = delta + log_h  # K x D
        best_d = mx.argmax(ends, axis=1)  # K
        M = mx.max(ends, axis=1)  # K
        cand = M[:, None] + log_A  # J x K
        best_j = mx.argmax(cand, axis=0)  # K
        enter = mx.max(cand, axis=0)  # K
        stay = mx.concatenate([neg_inf, (delta + log_1mh)[:, :-1]], axis=1)
        delta = mx.concatenate([enter[:, None], stay[:, 1:]], axis=1) + log_e[t][:, None]
        mx.eval(delta, best_d, best_j)
        back_d.append(best_d.tolist())
        back_j.append(best_j.tolist())
    flat = mx.argmax(delta.reshape(-1)).item()
    j, d = divmod(flat, D)
    path = [j]
    for t in range(T - 1, 0, -1):
        if d > 0:
            d -= 1
        else:
            pj = back_j[t - 1][j]
            j, d = pj, back_d[t - 1][pj]
        path.append(j)
    return path[::-1]


def viterbi(der: Derived, batch: Batch, n: int = 0, use_labels: bool = True) -> list[int]:
    """Viterbi path for sequence n of a batch. Inactive windows get -1;
    each restart begins a new independent segment."""
    from hsmm.forward import evidence

    e = evidence(der, batch.cat[n], batch.sw[n], batch.label_factor[n] if use_labels else None)
    act = batch.active[n].tolist()[: batch.lengths[n]]
    rs = batch.restart[n].tolist()
    out = [-1] * len(act)
    seg: list[int] = []

    def flush():
        if seg:
            for idx, s in zip(seg, viterbi_segment(der, e[mx.array(seg)])):
                out[idx] = s
            seg.clear()

    for t, a in enumerate(act):
        if a and rs[t]:
            flush()
        if a:
            seg.append(t)
    flush()
    return out
