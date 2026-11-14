"""Single place that decides which MLX device each workload runs on.

Live/online inference (one HSMM or GRU step per 2-min window) is tiny and
sequential, so GPU dispatch overhead would dominate: it is pinned to CPU.
Batched training may move to the GPU only after a benchmark shows it wins;
flip TRAIN_DEVICE here (and nowhere else) when that happens.
"""

from __future__ import annotations

import mlx.core as mx

LIVE_DEVICE = mx.cpu
TRAIN_DEVICE = mx.cpu  # benchmark before changing to mx.gpu


def live_stream():
    """Context manager for the live path: `with live_stream(): ...`."""
    return mx.stream(LIVE_DEVICE)


def train_stream():
    """Context manager for batched nightly work (training, replays)."""
    return mx.stream(TRAIN_DEVICE)
