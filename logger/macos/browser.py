"""Frontmost-browser URL + private-window flag via osascript.

Only called when the browser is already frontmost (so ``application id`` never launches it), with
a hard subprocess timeout so a hung browser cannot stall the sampler.
"""

from __future__ import annotations

import subprocess
from typing import Callable

from logger.sample import BrowserInfo

_SCRIPTS = {
    "chromium": 'tell application id "{bid}" to return (URL of active tab of front window) & linefeed & (mode of front window)',
    "arc": 'tell application id "{bid}" to return (URL of active tab of front window) & linefeed & (incognito of front window as text)',
    # Safari exposes no private-window flag to AppleScript.
    "safari": 'tell application id "{bid}" to return URL of current tab of front window',
}


def script_for(kind: str, bundle_id: str) -> str:
    return _SCRIPTS[kind].format(bid=bundle_id)


def parse_reply(kind: str, stdout: str) -> BrowserInfo:
    lines = stdout.strip().splitlines()
    url = lines[0].strip() if lines else None
    if not url or url == "missing value":
        url = None
    flag = lines[1].strip().lower() if len(lines) > 1 else ""
    private: bool | None = None
    if kind == "chromium":
        private = {"incognito": True, "normal": False}.get(flag)
    elif kind == "arc":
        private = {"true": True, "false": False}.get(flag)
    return BrowserInfo(url, private)


def query_browser(
    bundle_id: str,
    kind: str,
    timeout_s: float,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> BrowserInfo | None:
    try:
        res = run(["osascript", "-e", script_for(kind, bundle_id)], capture_output=True, text=True, timeout=timeout_s)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if res.returncode != 0:
        return None
    return parse_reply(kind, res.stdout)
