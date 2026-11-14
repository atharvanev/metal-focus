from logger.counters import SwitchCounter


def test_snapshot_returns_count_and_resets():
    c = SwitchCounter()
    for _ in range(3):
        c.on_app_switch()
    assert c.snapshot_and_reset() == 3
    assert c.snapshot_and_reset() == 0
