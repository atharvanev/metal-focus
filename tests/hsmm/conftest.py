import mlx.core as mx
import pytest

from hsmm.params import HSMMParams


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: long-running (EM recovery); deselect with -m 'not slow'")
    mx.set_default_device(mx.cpu)  # deterministic synthetic tests on the CPU stream


@pytest.fixture
def tiny_params():
    """K=3, D=4, 3 categories, 2 switch bins: small enough to brute-force."""
    return HSMMParams.create(
        pi=[0.5, 0.3, 0.2],
        A=[[0, 0.7, 0.3], [0.6, 0, 0.4], [0.5, 0.5, 0]],
        B=[[0.7, 0.2, 0.1], [0.2, 0.6, 0.2], [0.1, 0.3, 0.6]],
        C=[[0.8, 0.2], [0.4, 0.6], [0.3, 0.7]],
        mu=[2.5, 1.8, 3.0],
        r=[2.0, 5.0, 1.0],
        D=4,
    )


@pytest.fixture
def tiny_obs():
    # -1 = missing observation (e.g. category Other)
    return dict(cats=[0, 0, 2, -1, 1, 2, 0], sws=[0, 1, 1, 0, -1, 1, 0])
