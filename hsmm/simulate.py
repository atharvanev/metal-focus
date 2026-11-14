"""Sample synthetic days from known HSMM parameters (deterministic via keys)."""

from __future__ import annotations

import mlx.core as mx

from hsmm.batch import DayObs
from hsmm.params import HSMMParams


def default_true_params(D: int = 30) -> HSMMParams:
    """Plausible ground truth for tests: Working, Short break, Drifting, Rabbit hole.

    Categories: Coding, Docs, Chat, Video, Social. Switch bins: low, med, high.
    """
    return HSMMParams.create(
        pi=[0.7, 0.2, 0.05, 0.05],
        A=[
            [0.0, 0.7, 0.3, 0.0],
            [0.8, 0.0, 0.2, 0.0],
            [0.3, 0.0, 0.0, 0.7],
            [0.9, 0.1, 0.0, 0.0],
        ],
        B=[
            [0.55, 0.35, 0.06, 0.02, 0.02],
            [0.05, 0.05, 0.50, 0.15, 0.25],
            [0.05, 0.05, 0.20, 0.30, 0.40],
            [0.02, 0.02, 0.06, 0.60, 0.30],
        ],
        C=[
            [0.70, 0.25, 0.05],
            [0.30, 0.50, 0.20],
            [0.10, 0.30, 0.60],
            [0.50, 0.35, 0.15],
        ],
        mu=[12.0, 3.0, 3.0, 15.0],
        r=[3.0, 4.0, 4.0, 2.0],
        D=D,
    )


def sample_day(params: HSMMParams, T: int, key: mx.array, away_prob: float = 0.0) -> DayObs:
    """Sample T windows. Away windows (if away_prob > 0) are inserted between
    windows and do not consume model time, matching the frozen-belief semantics."""
    k_seg, k_cat, k_sw, k_away = mx.random.split(key, 4)
    log_pi, log_A = mx.log(params.pi), mx.log(params.A)
    log_p = mx.log(params.duration_pmf())
    states: list[int] = []
    seg_keys = mx.random.split(k_seg, 2 * T + 2)
    i = 0
    s = mx.random.categorical(log_pi, key=seg_keys[i]).item()
    while len(states) < T:
        d = mx.random.categorical(log_p[s], key=seg_keys[i + 1]).item() + 1
        states.extend([s] * d)
        s = mx.random.categorical(log_A[s], key=seg_keys[i + 2]).item()
        i += 2
    states = states[:T]  # last stretch is censored by end of day
    st = mx.array(states)
    cat = mx.random.categorical(mx.log(params.B)[st], key=k_cat).tolist()
    sw = mx.random.categorical(mx.log(params.C)[st], key=k_sw).tolist()

    if away_prob <= 0:
        return DayObs(cat=cat, sw=sw, states=states)
    # insert away runs (1-4 windows) before some windows
    u = mx.random.uniform(shape=(T, 2), key=k_away).tolist()
    oc, osw, oaway, ost = [], [], [], []
    for t in range(T):
        if u[t][0] < away_prob:
            n = 1 + int(u[t][1] * 4)
            oc += [-1] * n
            osw += [-1] * n
            oaway += [True] * n
            ost += [-1] * n
        oc.append(cat[t])
        osw.append(sw[t])
        oaway.append(False)
        ost.append(states[t])
    return DayObs(cat=oc, sw=osw, away=oaway, states=ost)


def sample_days(params: HSMMParams, n_days: int, T: int, seed: int = 0, away_prob: float = 0.0) -> list[DayObs]:
    keys = mx.random.split(mx.random.key(seed), n_days)
    return [sample_day(params, T, keys[i], away_prob) for i in range(n_days)]
