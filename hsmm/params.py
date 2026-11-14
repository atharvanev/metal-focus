"""HSMM parameters: pi, A, B, C, negative-binomial durations, hazards.

Stored parameters: pi (K), A (K x K, zero diagonal), B (K x n_cat),
C (K x n_bin), mu (K), r (K). Derived (never stored): duration pmf p (K x D)
and hazard h (K x D).

Durations: d = 1 + X, X ~ NegBin(r, mean mu - 1), truncated to d in 1..D and
renormalized. Hazard h_j(d) = p_j(d) / sum_{k>=d} p_j(k), with h_j(D) = 1.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import mlx.core as mx

MIN_MU = 1.0 + 1e-3
SURVIVAL_EPS = 1e-30


def nb_log_pmf(mu: mx.array, r: mx.array, D: int) -> mx.array:
    """Truncated, renormalized log pmf of the shifted NB, for d = 1..D.

    mu, r: arrays of any matching shape S. Returns shape S + (D,).
    Uses log p(X=k) = r log(r/(r+m)) + k log(m/(r+m)) + sum_{i<k} log((i+r)/(i+1))
    (no lgamma in MLX, the cumulative sum of log-ratios is exact).
    """
    mu = mx.maximum(mx.array(mu, dtype=mx.float32), MIN_MU)
    r = mx.array(r, dtype=mx.float32)
    m = (mu - 1.0)[..., None]
    rr = r[..., None]
    k = mx.arange(D, dtype=mx.float32)
    i = mx.arange(D - 1, dtype=mx.float32)
    log_ratio = mx.log((i + rr) / (i + 1.0))
    zero = mx.zeros(log_ratio.shape[:-1] + (1,), dtype=mx.float32)
    log_comb = mx.concatenate([zero, mx.cumsum(log_ratio, axis=-1)], axis=-1)
    logp = rr * mx.log(rr / (rr + m)) + k * mx.log(m / (rr + m)) + log_comb
    return logp - mx.logsumexp(logp, axis=-1, keepdims=True)


def hazard_from_pmf(p: mx.array) -> mx.array:
    """h(d) = p(d) / S(d), S(d) = sum_{k>=d} p(k); last column forced to 1."""
    surv = mx.cumsum(p, axis=-1, reverse=True)
    h = mx.clip(p / mx.maximum(surv, SURVIVAL_EPS), 0.0, 1.0)
    ones = mx.ones(h.shape[:-1] + (1,), dtype=h.dtype)
    return mx.concatenate([h[..., :-1], ones], axis=-1)


def floor_rows(M: mx.array, floor: float) -> mx.array:
    M = mx.maximum(M, floor)
    return M / M.sum(axis=-1, keepdims=True)


def normalize_transitions(A: mx.array, floor: float = 0.0) -> mx.array:
    K = A.shape[0]
    off = 1.0 - mx.eye(K)
    A = mx.maximum(A, floor) * off
    return A / A.sum(axis=-1, keepdims=True)


@dataclass(frozen=True)
class HSMMParams:
    pi: mx.array
    A: mx.array
    B: mx.array
    C: mx.array
    mu: mx.array
    r: mx.array
    D: int = 30

    @property
    def K(self) -> int:
        return self.pi.shape[0]

    def duration_pmf(self) -> mx.array:
        return mx.exp(nb_log_pmf(self.mu, self.r, self.D))

    def derived(self) -> "Derived":
        p = self.duration_pmf()
        return Derived(params=self, p=p, h=hazard_from_pmf(p))

    @staticmethod
    def create(pi, A, B, C, mu, r, D: int = 30, floor: float = 1e-4) -> "HSMMParams":
        f32 = lambda x: mx.array(x, dtype=mx.float32)  # noqa: E731
        # Floors keep every switch / start possible, so a hard label can never
        # make a day's likelihood exactly zero.
        return HSMMParams(
            pi=floor_rows(f32(pi), floor),
            A=normalize_transitions(f32(A), floor),
            B=floor_rows(f32(B), floor),
            C=floor_rows(f32(C), floor),
            mu=mx.clip(f32(mu), MIN_MU, float(D)),
            r=f32(r),
            D=D,
        )

    def to_dict(self) -> dict:
        d = {k: getattr(self, k).tolist() for k in ("pi", "A", "B", "C", "mu", "r")}
        d["D"] = self.D
        return d

    @staticmethod
    def from_dict(d: dict) -> "HSMMParams":
        return HSMMParams.create(d["pi"], d["A"], d["B"], d["C"], d["mu"], d["r"], d["D"])

    def save(self, path) -> None:
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @staticmethod
    def load(path) -> "HSMMParams":
        with open(path) as f:
            return HSMMParams.from_dict(json.load(f))


@dataclass(frozen=True)
class Derived:
    """Params plus the tables the recursions need (computed once per params)."""

    params: HSMMParams
    p: mx.array  # K x D duration pmf
    h: mx.array  # K x D hazard

    def __post_init__(self):
        P = self.params
        ones = lambda n: mx.ones((n, 1), dtype=mx.float32)  # noqa: E731
        # Extra all-ones column: index -1 (missing observation) -> factor 1.
        object.__setattr__(self, "Bx", mx.concatenate([P.B, ones(P.K)], axis=1))
        object.__setattr__(self, "Cx", mx.concatenate([P.C, ones(P.K)], axis=1))


def fit_truncated_nb(counts: mx.array, D: int, mu_grid=None, r_grid=None):
    """Maximum-likelihood (mu, r) per row of a K x D histogram over d = 1..D.

    Grid search over the truncated pmf so truncation at D is handled exactly
    (moment matching would be biased for long states). Vectorized in MLX.
    """
    if mu_grid is None:
        mu_grid = mx.linspace(1.05, float(D), 4 * D)
    if r_grid is None:
        r_grid = mx.exp(mx.linspace(mx.log(mx.array(0.3)).item(), mx.log(mx.array(60.0)).item(), 48))
    M, R = mu_grid.shape[0], r_grid.shape[0]
    mu_g = mx.broadcast_to(mu_grid[:, None], (M, R))
    r_g = mx.broadcast_to(r_grid[None, :], (M, R))
    logp = nb_log_pmf(mu_g, r_g, D).reshape(M * R, D)  # G x D
    ll = counts @ logp.T  # K x G
    best = mx.argmax(ll, axis=1)
    return mu_g.reshape(-1)[best], r_g.reshape(-1)[best]
