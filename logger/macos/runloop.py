"""Cocoa run loop: an NSTimer drives Sampler.tick(); NSWorkspace notifications count app switches
and record sleep/wake/lock events. There is no event tap and no keyboard/mouse hook of any kind."""

from __future__ import annotations

import logging
import signal
import time

import objc
from AppKit import (
    NSWorkspace,
    NSWorkspaceDidActivateApplicationNotification,
    NSWorkspaceDidWakeNotification,
    NSWorkspaceScreensDidSleepNotification,
    NSWorkspaceScreensDidWakeNotification,
    NSWorkspaceWillSleepNotification,
)
from Foundation import NSDistributedNotificationCenter, NSObject, NSTimer
from PyObjCTools import AppHelper

from logger.config import LoggerConfig
from logger.counters import SwitchCounter
from logger.macos.probes import MacProbes
from logger.sampler import SampleSink, Sampler

log = logging.getLogger("logger")


class _Observer(NSObject):
    def initWithSampler_switches_sink_(self, sampler, switches, sink):
        self = objc.super(_Observer, self).init()
        if self is None:
            return None
        self.sampler, self.switches, self.sink = sampler, switches, sink
        return self

    @objc.python_method
    def _event(self, kind: str) -> None:
        try:
            self.sink.write_event(time.time(), kind)
        except Exception:
            log.exception("failed to write %s event", kind)

    def appActivated_(self, note):
        self.switches.on_app_switch()

    def willSleep_(self, note):
        self._event("sleep")

    def didWake_(self, note):
        self._event("wake")

    def screensSlept_(self, note):
        self._event("screens_sleep")

    def screensWoke_(self, note):
        self._event("screens_wake")

    def locked_(self, note):
        self._event("lock")

    def unlocked_(self, note):
        self._event("unlock")

    def tick_(self, timer):
        try:
            self.sampler.tick()
        except Exception:
            log.exception("tick failed; continuing")


def run(cfg: LoggerConfig, sink: SampleSink) -> None:
    switches = SwitchCounter()
    sampler = Sampler(MacProbes(cfg), switches, sink, cfg)
    obs = _Observer.alloc().initWithSampler_switches_sink_(sampler, switches, sink)

    ws = NSWorkspace.sharedWorkspace().notificationCenter()
    for selector, name in [
        ("appActivated:", NSWorkspaceDidActivateApplicationNotification),
        ("willSleep:", NSWorkspaceWillSleepNotification),
        ("didWake:", NSWorkspaceDidWakeNotification),
        ("screensSlept:", NSWorkspaceScreensDidSleepNotification),
        ("screensWoke:", NSWorkspaceScreensDidWakeNotification),
    ]:
        ws.addObserver_selector_name_object_(obs, selector, name, None)
    dn = NSDistributedNotificationCenter.defaultCenter()
    dn.addObserver_selector_name_object_(obs, "locked:", "com.apple.screenIsLocked", None)
    dn.addObserver_selector_name_object_(obs, "unlocked:", "com.apple.screenIsUnlocked", None)

    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        cfg.sample_interval_s, obs, "tick:", None, True
    )
    signal.signal(signal.SIGTERM, lambda *_: AppHelper.stopEventLoop())

    sink.write_event(time.time(), "logger_start")
    log.info("logger started (every %.1f s)", cfg.sample_interval_s)
    try:
        AppHelper.runConsoleEventLoop(installInterrupt=True)
    finally:
        sink.write_event(time.time(), "logger_stop")
        log.info("logger stopped")
