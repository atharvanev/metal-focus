"""The training-device benchmark: device override plumbing and a tiny CPU-only run."""

import mlx.core as mx

import common.device as device
from hsmm import bench


def test_override_train_device_restores():
    before = device.TRAIN_DEVICE
    with device.override_train_device("cpu"):
        assert device.TRAIN_DEVICE == mx.cpu
    assert device.TRAIN_DEVICE == before


def test_override_train_device_restores_on_error():
    before = device.TRAIN_DEVICE
    try:
        with device.override_train_device("cpu"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert device.TRAIN_DEVICE == before


def test_device_names():
    assert device.device_by_name("cpu") == mx.cpu
    assert device.device_by_name("gpu") == mx.gpu


def test_tiny_bench_cpu_only():
    res = bench.run(n_days=[2], T=20, repeats=1, devices=["cpu"], em_iters=2, warmup=0)
    names = {r["workload"] for r in res["rows"]}
    assert names == {"nightly_update", "fit_em", "live_step"}
    for r in res["rows"]:
        assert r["device"] == "cpu"
        assert r["median_s"] > 0
    md = bench.to_markdown(res)
    assert "nightly_update" in md and "| cpu |" in md
