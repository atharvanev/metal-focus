"""Single place that decides which MLX device each workload runs on.

Live/online inference (one HSMM or GRU step per 2-min window) is tiny and
sequential, so GPU dispatch overhead would dominate: it is pinned to CPU.
Batched training may move to the GPU only after a benchmark shows it wins;
flip TRAIN_DEVICE here (and nowhere else) when that happens.
"""

from __future__ import annotations

from contextlib import contextmanager

import mlx.core as mx

LIVE_DEVICE = mx.cpu
TRAIN_DEVICE = mx.cpu  # GPU ~2.7x slower: docs/benchmarks/2026-11-13-hsmm-train-device.md


_DEVICES = {"cpu": mx.cpu, "gpu": mx.gpu}


def device_by_name(name: str):
    """"cpu" | "gpu" -> MLX device (for tools that compare devices, e.g. hsmm.bench)."""
    return _DEVICES[name]


@contextmanager
def override_train_device(name: str):
    """Temporarily point train_stream() at another device. Benchmarks only;
    the production choice is TRAIN_DEVICE above."""
    global TRAIN_DEVICE
    old = TRAIN_DEVICE
    TRAIN_DEVICE = device_by_name(name)
    try:
        yield
    finally:
        TRAIN_DEVICE = old


def live_stream():
    """Context manager for the live path: `with live_stream(): ...`."""
    return mx.stream(LIVE_DEVICE)


def train_stream():
    """Context manager for batched nightly work (training, replays)."""
    return mx.stream(TRAIN_DEVICE)
