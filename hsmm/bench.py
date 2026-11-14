"""CPU vs GPU benchmark for the HSMM's MLX workloads.

    uv run python -m hsmm.bench [--days 7 14 28] [--T 720] [--repeats 3] [--out FILE.md]

CLAUDE.md: batched training may move to the GPU stream only if a benchmark
shows it wins. This times, on synthetic days from hsmm.simulate:
- nightly_update: the real nightly E-step + update (hsmm.train.nightly_update)
  on N days of T windows, with train_stream() pointed at each device;
- fit_em: seconds per EM iteration (unsupervised fitting / restarts);
- live_step: one forward_step for one sequence (the live path, for reference).
It also reports the largest parameter difference between devices, so a
"faster" device that gives different answers is caught.
"""

from __future__ import annotations

import argparse
import platform
import statistics
import time
from datetime import date

import mlx.core as mx

from common.device import device_by_name, override_train_device
from hsmm.config import load_config
from hsmm.forward import evidence, forward_step
from hsmm.io import days_to_batch
from hsmm.simulate import default_true_params, sample_days
from hsmm.train import ZERO_PRIOR, fit_em, nightly_update

PARAM_KEYS = ("pi", "A", "B", "C", "mu", "r")


def _perturbed(params):
    """Starting point a little off the truth, so updates do real work."""
    from hsmm.params import HSMMParams

    B = params.B * 0.8 + 0.2 / params.B.shape[1]
    C = params.C * 0.8 + 0.2 / params.C.shape[1]
    return HSMMParams.create(params.pi, params.A, B, C, params.mu * 0.8, params.r, D=params.D)


def _time(fn, repeats: int, warmup: int) -> list[float]:
    for _ in range(warmup):
        fn()
    out = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        out.append(time.perf_counter() - t0)
    return out


def _row(workload: str, dev: str, n_days: int | None, T: int | None, times: list[float], **extra) -> dict:
    return {
        "workload": workload,
        "device": dev,
        "n_days": n_days,
        "T": T,
        "median_s": statistics.median(times),
        "min_s": min(times),
        **extra,
    }


def run(
    n_days: list[int] | None = None,
    T: int = 720,
    repeats: int = 3,
    devices: list[str] | None = None,
    em_iters: int = 3,
    live_steps: int = 300,
    warmup: int = 1,
    seed: int = 0,
) -> dict:
    n_days = n_days or [7, 14, 28]
    devices = devices or ["cpu", "gpu"]
    cfg = load_config()
    truth = default_true_params()
    start = _perturbed(truth)
    rows: list[dict] = []
    max_diff: dict[int, float] = {}

    for n in n_days:
        batch = days_to_batch(sample_days(truth, n, T, seed=seed), cfg)
        results = {}
        for dev in devices:
            with override_train_device(dev):
                new, _ = nightly_update(start, batch, cfg)  # also the warm-up result we compare
                times = _time(lambda: nightly_update(start, batch, cfg), repeats, max(0, warmup - 1))
                rows.append(_row("nightly_update", dev, n, T, times))
                results[dev] = new

                em_times = _time(lambda: fit_em(start, batch, n_iter=em_iters, tol=-1.0, prior=ZERO_PRIOR), repeats, 0)
                rows.append(_row("fit_em", dev, n, T, [t / em_iters for t in em_times], per="iteration"))
        if len(results) > 1:
            a, b = (results[d] for d in devices[:2])
            max_diff[n] = max(float(mx.abs(getattr(a, k) - getattr(b, k)).max().item()) for k in PARAM_KEYS)

    der = truth.derived()
    alpha0 = mx.zeros((1, truth.K, truth.D))
    e = evidence(der, mx.array([[0]], dtype=mx.int32), mx.array([[1]], dtype=mx.int32))[:, 0]
    act, rs0, rs1 = mx.array([True]), mx.array([True]), mx.array([False])
    for dev in devices:
        with mx.stream(device_by_name(dev)):

            def steps():
                a, _ = forward_step(der, alpha0, e, act, rs0)
                for _ in range(live_steps):
                    a, lc = forward_step(der, a, e, act, rs1)
                    mx.eval(a, lc)

            times = _time(steps, repeats, warmup)
            rows.append(_row("live_step", dev, None, None, [t / live_steps for t in times], per="step"))

    return {
        "date": date.today().isoformat(),
        "machine": f"{_chip()}, macOS {platform.mac_ver()[0]}",
        "mlx": mx.__version__,
        "T": T,
        "repeats": repeats,
        "rows": rows,
        "max_param_diff": max_diff,
    }


def _chip() -> str:
    import subprocess

    try:
        return subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, timeout=2).stdout.strip() or platform.machine()
    except Exception:
        return platform.machine()


def _fmt(s: float) -> str:
    return f"{s * 1e3:.3f} ms" if s < 0.1 else f"{s:.2f} s"


def to_markdown(res: dict) -> str:
    lines = [
        f"# HSMM training device benchmark ({res['date']})",
        "",
        f"- Machine: {res['machine']}; MLX {res['mlx']}",
        f"- Synthetic days from `hsmm.simulate.default_true_params`, T = {res['T']} windows/day; median of {res['repeats']} runs",
        "",
        "| workload | device | days | median | min | per |",
        "|---|---|---|---|---|---|",
    ]
    for r in res["rows"]:
        lines.append(
            f"| {r['workload']} | {r['device']} | {r['n_days'] if r['n_days'] is not None else '-'} | "
            f"{_fmt(r['median_s'])} | {_fmt(r['min_s'])} | {r.get('per', 'call')} |"
        )
    speed = _speedups(res)
    if speed:
        lines += ["", "GPU speedup (CPU median / GPU median; >1 means GPU is faster):", ""]
        lines += [f"- {k}: {v:.2f}x" for k, v in speed.items()]
    if res["max_param_diff"]:
        lines += ["", "Largest |param_cpu - param_gpu| after one nightly_update:", ""]
        lines += [f"- {n} days: {d:.2e}" for n, d in res["max_param_diff"].items()]
    return "\n".join(lines) + "\n"


def _speedups(res: dict) -> dict[str, float]:
    by = {(r["workload"], r["n_days"], r["device"]): r["median_s"] for r in res["rows"]}
    out = {}
    for (w, n, d), cpu in by.items():
        if d == "cpu" and (w, n, "gpu") in by:
            out[f"{w}" + (f" ({n} days)" if n else "")] = cpu / by[(w, n, "gpu")]
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, nargs="+", default=[7, 14, 28])
    ap.add_argument("--T", type=int, default=720)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--em-iters", type=int, default=3)
    ap.add_argument("--devices", nargs="+", default=["cpu", "gpu"], choices=["cpu", "gpu"])
    ap.add_argument("--out", help="also write the markdown report here")
    a = ap.parse_args(argv)
    md = to_markdown(run(a.days, a.T, a.repeats, a.devices, a.em_iters))
    print(md)
    if a.out:
        with open(a.out, "w") as f:
            f.write(md)


if __name__ == "__main__":
    main()
