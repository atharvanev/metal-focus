"""Pure-Python HSMM reference by enumerating every state sequence.

Semantics (must match the MLX recursions):
- window 1 starts a fresh stretch drawn from pi (d = 1);
- every completed stretch of length L contributes p_j(L) (truncated at D);
- the last stretch is right-censored: contributes survival S_j(L) = sum_{k>=L} p_j(k);
- consecutive stretches differ (A has a zero diagonal), so runs == stretches.
Only usable for tiny K, T.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass


def _runs(states):
    runs = []
    for s in states:
        if runs and runs[-1][0] == s:
            runs[-1][1] += 1
        else:
            runs.append([s, 1])
    return runs


@dataclass
class Brute:
    Z: float
    filt_last: list[float]  # P(s_T = j | all)
    smooth: list[list[float]]  # T x K
    trans: list[list[float]]  # K x K expected switch counts
    dur: list[list[float]]  # K x D expected completed-stretch length counts
    best: tuple[int, ...]
    best_logp: float


def brute(params, cats, sws, label_factor=None) -> Brute:
    P = params.to_dict()
    K, D = len(P["pi"]), P["D"]
    p = params.duration_pmf().tolist()
    surv = [[sum(p[j][k:]) for k in range(D)] for j in range(K)]
    B, C, A, pi = P["B"], P["C"], P["A"], P["pi"]
    T = len(cats)

    def emit(j, t):
        e = (B[j][cats[t]] if cats[t] >= 0 else 1.0) * (C[j][sws[t]] if sws[t] >= 0 else 1.0)
        if label_factor is not None:
            e *= label_factor[t][j]
        return e

    Z = 0.0
    filt = [0.0] * K
    smooth = [[0.0] * K for _ in range(T)]
    trans = [[0.0] * K for _ in range(K)]
    dur = [[0.0] * D for _ in range(K)]
    best, best_lp = None, -math.inf
    for states in itertools.product(range(K), repeat=T):
        runs = _runs(states)
        prob = pi[runs[0][0]]
        for i, (s, L) in enumerate(runs):
            last = i == len(runs) - 1
            if L > D:
                prob = 0.0
                break
            prob *= surv[s][L - 1] if last else p[s][L - 1]
            if not last:
                prob *= A[s][runs[i + 1][0]]
        if prob == 0.0:
            continue
        for t, s in enumerate(states):
            prob *= emit(s, t)
        if prob == 0.0:
            continue
        Z += prob
        filt[states[-1]] += prob
        for t, s in enumerate(states):
            smooth[t][s] += prob
        for i in range(len(runs) - 1):
            trans[runs[i][0]][runs[i + 1][0]] += prob
            dur[runs[i][0]][runs[i][1] - 1] += prob
        lp = math.log(prob)
        if lp > best_lp:
            best, best_lp = states, lp
    return Brute(
        Z=Z,
        filt_last=[f / Z for f in filt],
        smooth=[[x / Z for x in row] for row in smooth],
        trans=[[x / Z for x in row] for row in trans],
        dur=[[x / Z for x in row] for row in dur],
        best=best,
        best_logp=best_lp,
    )
