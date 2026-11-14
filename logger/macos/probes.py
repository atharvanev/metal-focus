"""Real macOS probes. Each returns None (never raises) when a permission is missing.

Window titles come from the Accessibility API (needs Accessibility), not CGWindowList (which would
need Screen Recording). Input activity comes only from CGEventSourceSecondsSinceLastEventType:
seconds since the last key press / click / scroll, no content and no Input Monitoring permission.
There is deliberately no event tap or global input monitor anywhere in the logger.
"""

from __future__ import annotations

import ApplicationServices as AX
import Quartz
from AppKit import NSWorkspace

from logger.config import LoggerConfig
from logger.macos.browser import query_browser
from logger.sample import AppInfo, BrowserInfo, InputRecency, WindowInfo

_HID = Quartz.kCGEventSourceStateHIDSystemState
_AX_TIMEOUT_S = 0.5  # a hung app must not stall the sampler


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

    def screen_locked(self) -> bool:
        d = Quartz.CGSessionCopyCurrentDictionary() or {}
        return bool(d.get("CGSSessionScreenIsLocked", False))
