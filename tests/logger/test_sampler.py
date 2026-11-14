import logging

from logger.config import load_config
from logger.counters import SwitchCounter
from logger.sample import AppInfo, BrowserInfo, InputRecency, WindowInfo
from logger.sampler import Sampler

VSCODE = AppInfo("com.microsoft.VSCode", "Code", 12)
CHROME = AppInfo("com.google.Chrome", "Google Chrome", 10)


class FakeProbes:
    def __init__(self, **values):
        self.values = {
            "frontmost": VSCODE, "window": WindowInfo("file.py", False),
            "browser": BrowserInfo("https://x.com", False), "idle_seconds": 0.5,
            "input_recency": InputRecency(1.0, 1.0, 1.0), "screen_locked": False, "media_playing": False,
        }
        self.values.update(values)
        self.calls = []

    def _get(self, name):
        self.calls.append(name)
        v = self.values[name]
        if isinstance(v, BaseException):
            raise v
        return v

    def frontmost(self): return self._get("frontmost")
    def window(self, pid): return self._get("window")
    def browser(self, bundle_id): return self._get("browser")
    def idle_seconds(self): return self._get("idle_seconds")
    def input_recency(self): return self._get("input_recency")
    def screen_locked(self): return self._get("screen_locked")
    def media_playing(self, app): return self._get("media_playing")


class FakeSink:
    def __init__(self):
        self.samples, self.events = [], []

    def write_sample(self, s): self.samples.append(s)
    def write_event(self, ts, kind, detail=None): self.events.append((ts, kind, detail))


def make(probes, clock_values=(1000.0, 1004.0, 1008.0), switches=None):
    clock = iter(clock_values)
    sink = FakeSink()
    s = Sampler(probes, switches or SwitchCounter(), sink, load_config(), clock=lambda: next(clock), tz_offset=lambda: -480)
    return s, sink


def test_tick_writes_sample_with_counts():
    sw = SwitchCounter()
    sw.on_app_switch(); sw.on_app_switch()
    s, sink = make(FakeProbes(), switches=sw)
    s.tick()
    assert len(sink.samples) == 1
    out = sink.samples[0]
    assert (out.bundle_id, out.app_switches, out.dt, out.tz_offset_min) == ("com.microsoft.VSCode", 2, 4.0, -480)


def test_browser_probe_only_for_browsers():
    p = FakeProbes()
    s, _ = make(p)
    s.tick()
    assert "browser" not in p.calls
    p2 = FakeProbes(frontmost=CHROME)
    s2, sink2 = make(p2)
    s2.tick()
    assert "browser" in p2.calls and sink2.samples[0].domain == "x.com"


def test_dt_is_elapsed_time():
    s, sink = make(FakeProbes(), (1000.0, 1004.5))
    s.tick(); s.tick()
    assert sink.samples[1].dt == 4.5


def test_gap_records_event_and_caps_dt():
    s, sink = make(FakeProbes(), (1000.0, 1004.0, 4604.0))
    for _ in range(3):
        s.tick()
    assert sink.events == [(1004.0, "gap", "3600.0s")]
    assert sink.samples[2].dt == 4.0


def test_probe_exceptions_become_nulls(caplog):
    p = FakeProbes(window=PermissionError("no AX"), idle_seconds=RuntimeError("boom"))
    s, sink = make(p)
    with caplog.at_level(logging.WARNING, logger="logger"):
        for _ in range(3):
            s.tick()
    assert len(sink.samples) == 3
    assert sink.samples[0].window_title is None and sink.samples[0].idle_s is None
    msgs = [r.getMessage() for r in caplog.records]
    assert sum("window" in m for m in msgs) == 1
    assert sum("idle_seconds" in m for m in msgs) == 1


def test_no_permissions_at_all():
    p = FakeProbes(frontmost=None, window=None, browser=None, idle_seconds=None,
                   input_recency=None, screen_locked=OSError("x"))
    s, sink = make(p)
    for _ in range(3):
        s.tick()
    assert len(sink.samples) == 3
    assert all(x.bundle_id is None and x.key_active is None for x in sink.samples)


def test_locked_skips_window_and_browser():
    p = FakeProbes(frontmost=CHROME, screen_locked=True)
    s, sink = make(p)
    s.tick()
    assert "window" not in p.calls and "browser" not in p.calls
    assert sink.samples[0].locked == 1


def test_clock_jump_backwards_never_gives_negative_dt():
    s, sink = make(FakeProbes(), (1000.0, 990.0))
    s.tick(); s.tick()
    assert sink.samples[1].dt == 4.0
    assert sink.events and sink.events[0][1] == "gap"


def test_media_playing_flag_recorded():
    s, sink = make(FakeProbes(media_playing=True))
    s.tick()
    assert sink.samples[0].media_playing == 1


def test_media_probe_skipped_when_locked_and_failure_is_null():
    probes = FakeProbes(screen_locked=True, media_playing=True)
    s, sink = make(probes)
    s.tick()
    assert "media_playing" not in probes.calls and sink.samples[0].media_playing is None
    s, sink = make(FakeProbes(media_playing=RuntimeError("iokit")))
    s.tick()
    assert sink.samples[0].media_playing is None
