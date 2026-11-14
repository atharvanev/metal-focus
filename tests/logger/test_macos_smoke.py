"""Smoke tests against the real macOS APIs (run locally: uv run pytest -m macos)."""

import pytest

from logger.config import load_config
from logger.sample import InputRecency, WindowInfo

pytestmark = pytest.mark.macos


@pytest.fixture(scope="module")
def probes():
    from logger.macos.probes import MacProbes

    return MacProbes(load_config())


def test_frontmost_returns_app(probes):
    app = probes.frontmost()
    assert isinstance(app.bundle_id, str) and app.bundle_id
    assert app.pid > 0


def test_idle_is_nonnegative_float(probes):
    v = probes.idle_seconds()
    assert isinstance(v, float) and v >= 0


def test_input_recency_floats(probes):
    r = probes.input_recency()
    assert isinstance(r, InputRecency)
    assert all(isinstance(x, float) and x >= 0 for x in (r.key_s, r.click_s, r.scroll_s))


def test_screen_locked_is_bool(probes):
    assert probes.screen_locked() in (True, False)


def test_window_none_or_info(probes):
    w = probes.window(probes.frontmost().pid)
    assert w is None or isinstance(w, WindowInfo)


def test_report_keys():
    from logger.macos.permissions import report

    r = report(load_config())
    assert "accessibility" in r and "input_monitoring" not in r
    assert all(v in (True, False, None) for v in r.values())
