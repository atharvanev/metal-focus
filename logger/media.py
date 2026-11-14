"""Is the frontmost app playing video? Read from power assertions, not from input or screen content.

Browsers and video players take a ``PreventUserIdleDisplaySleep`` power assertion while a video plays
(that is what stops the screen dimming) and drop it on pause. Audio alone takes only a system-sleep
assertion, so background music does not count, and neither does ``caffeinate`` (its own process).

An assertion counts when it belongs to the frontmost app: the owning process is the app, is a helper
inside the app's bundle (Chrome's "Google Chrome Helper"), or created it on the app's behalf
(``AssertionOnBehalfOfPID``, used by WebKit's media processes for Safari).

This module is pure (no macOS imports) and is the reference for the Swift port in
``app/.../MediaPlayback.swift``; both are checked against ``tests/fixtures/media_cases.json``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

DISPLAY_ASSERTIONS = frozenset({"PreventUserIdleDisplaySleep", "NoDisplaySleepAssertion"})


def frontmost_plays_media(
    assertions: Iterable[Mapping[str, Any]],
    front_pid: int,
    front_bundle_path: str | None,
    path_of: Callable[[int], str | None],
) -> bool:
    """``assertions``: flat list of IOPMCopyAssertionsByProcess entries (AssertPID, AssertType, ...)."""
    prefix = front_bundle_path.rstrip("/") + "/" if front_bundle_path else None

    def is_front(pid: Any) -> bool:
        if not isinstance(pid, int):
            return False
        if pid == front_pid:
            return True
        path = path_of(pid) if prefix else None
        return bool(path and path.startswith(prefix))

    for a in assertions:
        kind = a.get("AssertionTrueType") or a.get("AssertType")
        if kind not in DISPLAY_ASSERTIONS:
            continue
        if is_front(a.get("AssertPID")) or is_front(a.get("AssertionOnBehalfOfPID")):
            return True
    return False
