"""Real macOS probes. Each returns None (never raises) when a permission is missing.

Video playback comes from power assertions (no permission; see logger/media.py).
Window titles come from the Accessibility API (needs Accessibility), not CGWindowList (which would
need Screen Recording). Input activity comes only from CGEventSourceSecondsSinceLastEventType:
seconds since the last key press / click / scroll, no content and no Input Monitoring permission.
There is deliberately no event tap or global input monitor anywhere in the logger.
"""

from __future__ import annotations

import ctypes

import ApplicationServices as AX
import objc
import Quartz
from AppKit import NSRunningApplication, NSWorkspace
from Foundation import NSBundle

from logger.config import LoggerConfig
from logger.macos.browser import query_browser
from logger.media import frontmost_plays_media
from logger.sample import AppInfo, BrowserInfo, InputRecency, WindowInfo

_HID = Quartz.kCGEventSourceStateHIDSystemState
_AX_TIMEOUT_S = 0.5  # a hung app must not stall the sampler


# Power assertions are readable without any permission (it's what `pmset -g assertions` shows).
_IOKIT: dict = {}
objc.loadBundleFunctions(NSBundle.bundleWithIdentifier_("com.apple.framework.IOKit"), _IOKIT,
                         [("IOPMCopyAssertionsByProcess", b"io^@")])
_LIBPROC = ctypes.CDLL("/usr/lib/libproc.dylib")


def _pid_path(pid: int) -> str | None:
    buf = ctypes.create_string_buffer(4096)
    n = _LIBPROC.proc_pidpath(int(pid), buf, ctypes.c_uint32(4096))
    return buf.value.decode(errors="replace") if n > 0 else None


def _ax_attr(element, attr):
    err, value = AX.AXUIElementCopyAttributeValue(element, attr, None)
    return value if err == 0 else None


class MacProbes:
    def __init__(self, cfg: LoggerConfig):
        self.cfg = cfg

    def frontmost(self) -> AppInfo | None:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return None
        return AppInfo(app.bundleIdentifier(), app.localizedName(), int(app.processIdentifier()))

    def window(self, pid: int) -> WindowInfo | None:
        if not AX.AXIsProcessTrusted():
            return None
        app_el = AX.AXUIElementCreateApplication(pid)
        AX.AXUIElementSetMessagingTimeout(app_el, _AX_TIMEOUT_S)
        win = _ax_attr(app_el, AX.kAXFocusedWindowAttribute)
        if win is None:
            return WindowInfo(None, None)
        title = _ax_attr(win, AX.kAXTitleAttribute)
        fullscreen = _ax_attr(win, "AXFullScreen")
        return WindowInfo(str(title) if title else None, bool(fullscreen) if fullscreen is not None else None)

    def browser(self, bundle_id: str) -> BrowserInfo | None:
        return query_browser(bundle_id, self.cfg.browsers[bundle_id], self.cfg.browser_timeout_s)

    def idle_seconds(self) -> float | None:
        return float(Quartz.CGEventSourceSecondsSinceLastEventType(_HID, Quartz.kCGAnyInputEventType))

    def input_recency(self) -> InputRecency | None:
        since = Quartz.CGEventSourceSecondsSinceLastEventType
        return InputRecency(
            key_s=float(since(_HID, Quartz.kCGEventKeyDown)),
            click_s=float(since(_HID, Quartz.kCGEventLeftMouseDown)),
            scroll_s=float(since(_HID, Quartz.kCGEventScrollWheel)),
        )

    def media_playing(self, app: AppInfo) -> bool | None:
        err, by_pid = _IOKIT["IOPMCopyAssertionsByProcess"](None)
        if err != 0 or by_pid is None:
            return None
        flat = [dict(a) for entries in by_pid.values() for a in entries]
        running = NSRunningApplication.runningApplicationWithProcessIdentifier_(app.pid)
        url = running.bundleURL() if running is not None else None
        return frontmost_plays_media(flat, app.pid, url.path() if url is not None else None, _pid_path)

    def screen_locked(self) -> bool:
        d = Quartz.CGSessionCopyCurrentDictionary() or {}
        return bool(d.get("CGSSessionScreenIsLocked", False))
