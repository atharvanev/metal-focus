"""One sampling step: query probes, apply privacy rules, write a row. No macOS imports here."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Callable, Protocol

from logger.config import LoggerConfig
from logger.counters import SwitchCounter
from logger.sample import AppInfo, BrowserInfo, InputRecency, Sample, WindowInfo, build_sample

log = logging.getLogger("logger")


class Probes(Protocol):
    def frontmost(self) -> AppInfo | None: ...
    def window(self, pid: int) -> WindowInfo | None: ...
    def browser(self, bundle_id: str) -> BrowserInfo | None: ...
    def idle_seconds(self) -> float | None: ...
    def input_recency(self) -> InputRecency | None: ...
    def screen_locked(self) -> bool: ...
    def media_playing(self, app: AppInfo) -> bool | None: ...


class SampleSink(Protocol):
    def write_sample(self, sample: Sample) -> None: ...
    def write_event(self, ts: float, kind: str, detail: str | None = None) -> None: ...


def local_tz_offset_min() -> int:
    off = datetime.now().astimezone().utcoffset()
    return int(off.total_seconds() // 60) if off else 0


class Sampler:
    def __init__(
        self,
        probes: Probes,
        switches: SwitchCounter,
        sink: SampleSink,
        cfg: LoggerConfig,
        clock: Callable[[], float] = time.time,
        tz_offset: Callable[[], int] = local_tz_offset_min,
    ) -> None:
        self.probes, self.switches, self.sink, self.cfg = probes, switches, sink, cfg
        self.clock, self.tz_offset = clock, tz_offset
        self._last: float | None = None
        self._warned: set[str] = set()

    def _safe(self, name: str, fn: Callable[..., Any], *args: Any, default: Any = None) -> Any:
        try:
            return fn(*args)
        except Exception as e:  # a failing probe must never stop the logger
            if name not in self._warned:
                self._warned.add(name)
                log.warning("probe %s failed (%s); storing NULL", name, e)
            return default

    def tick(self) -> Sample:
        now = self.clock()
        dt = self.cfg.sample_interval_s
        if self._last is not None:
            elapsed = now - self._last
            if elapsed > self.cfg.max_gap_s or elapsed <= 0:  # sleep/lid closed, or wall clock moved backwards
                self.sink.write_event(self._last, "gap", f"{elapsed:.1f}s")
            else:
                dt = elapsed
        self._last = now

        p = self.probes
        locked = bool(self._safe("screen_locked", p.screen_locked, default=False))
        app = self._safe("frontmost", p.frontmost)
        window = browser = media = None
        if not locked and app is not None:
            window = self._safe("window", p.window, app.pid)
            media = self._safe("media_playing", p.media_playing, app)
            if app.bundle_id in self.cfg.browsers:
                browser = self._safe("browser", p.browser, app.bundle_id)

        sample = build_sample(
            ts=now,
            tz_offset_min=self.tz_offset(),
            dt=dt,
            app=app,
            window=window,
            browser=browser,
            idle_s=self._safe("idle_seconds", p.idle_seconds),
            recency=self._safe("input_recency", p.input_recency),
            locked=locked,
            app_switches=self.switches.snapshot_and_reset(),
            cfg=self.cfg,
            media_playing=media,
        )
        self.sink.write_sample(sample)
        return sample
