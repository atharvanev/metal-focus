"""Turn per-day observation lists into padded MLX arrays for the recursions.

Away handling (explicit decision):
- Away windows are *inactive*: the belief is frozen (no time passes, no
  evidence, c_t = 1). Elapsed time in a distraction state therefore does not
  grow while I'm away, so Away can never make something look like a rabbit hole.
- A run of >= away_break_windows Away windows is a sequence break: the next
  active window restarts from pi, and the stretch before the break is censored
  (no segment end is counted for it).
- Padding at the end of shorter days is just more inactive windows.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import mlx.core as mx

HARD = "hard"
SOFT = "soft"


@dataclass
class DayObs:
    """One day (or any contiguous sequence) of windows.

    cat: category index per window, -1 = missing (Other/Excluded/unknown)
    sw: switch bin per window, -1 = missing
    away: True if the window is Away (frozen, not evidence)
    label: state index per window, -1 = unlabeled
    clamp: per window "hard" | "soft" | None (how strongly label is applied)
    weight: per window count weight (3.0 for check-ins)
    """

    cat: list[int]
    sw: list[int]
    away: list[bool] | None = None
    label: list[int] | None = None
    clamp: list[str | None] | None = None
    weight: list[float] | None = None
    states: list[int] | None = field(default=None, repr=False)  # ground truth (simulation only)

    def __len__(self) -> int:
        return len(self.cat)


@dataclass
class Batch:
    cat: mx.array  # N x T int32, -1 missing
    sw: mx.array  # N x T int32, -1 missing
    active: mx.array  # N x T bool
    restart: mx.array  # N x T bool (start from pi at this window)
    label_factor: mx.array  # N x T x K float32 multiplicative clamp
    weight: mx.array  # N x T float32 (weight of the window itself)
    weight_eff: mx.array  # N x T float32 (weight of last active window <= t)
    lengths: list[int]

    @property
    def N(self) -> int:
        return self.cat.shape[0]

    @property
    def T(self) -> int:
        return self.cat.shape[1]


def build_batch(
    days: list[DayObs],
    K: int = 4,
    away_break_windows: int = 15,
    soft_on: float = 0.9,
    soft_off: float = 0.1,
    hard_off: float = 1e-6,
) -> Batch:
    T = max(len(d) for d in days)
    cats, sws, acts, rss, labs, ws, weffs = [], [], [], [], [], [], []
    for d in days:
        n = len(d)
        away = list(d.away) if d.away is not None else [False] * n
        label = list(d.label) if d.label is not None else [-1] * n
        clamp = list(d.clamp) if d.clamp is not None else [None] * n
        weight = list(d.weight) if d.weight is not None else [1.0] * n

        active, restart, weff = [], [], []
        started, away_run, last_w = False, 0, 1.0
        for t in range(n):
            if away[t]:
                away_run += 1
                active.append(False)
                restart.append(False)
            else:
                rs = (not started) or away_run >= away_break_windows
                started, away_run, last_w = True, 0, weight[t]
                active.append(True)
                restart.append(rs)
            weff.append(last_w)

        lf = []
        for t in range(n):
            row = [1.0] * K
            if label[t] >= 0 and clamp[t] in (HARD, SOFT) and not away[t]:
                on, off = (1.0, hard_off) if clamp[t] == HARD else (soft_on, soft_off)
                row = [off] * K
                row[label[t]] = on
            lf.append(row)

        pad = T - n
        cats.append(list(d.cat) + [-1] * pad)
        sws.append(list(d.sw) + [-1] * pad)
        acts.append(active + [False] * pad)
        rss.append(restart + [False] * pad)
        labs.append(lf + [[1.0] * K] * pad)
        ws.append(weight + [0.0] * pad)
        weffs.append(weff + [weff[-1] if weff else 1.0] * pad)

    return Batch(
        cat=mx.array(cats, dtype=mx.int32),
        sw=mx.array(sws, dtype=mx.int32),
        active=mx.array(acts, dtype=mx.bool_),
        restart=mx.array(rss, dtype=mx.bool_),
        label_factor=mx.array(labs, dtype=mx.float32),
        weight=mx.array(ws, dtype=mx.float32),
        weight_eff=mx.array(weffs, dtype=mx.float32),
        lengths=[len(d) for d in days],
    )
