"""Permission preflight. The logger needs Accessibility (window titles) and Automation per browser
(tab URL). It never needs or requests Input Monitoring or Screen Recording."""

from __future__ import annotations

import subprocess

import ApplicationServices as AX
from AppKit import NSWorkspace

from logger.config import LoggerConfig


def _running_bundle_ids() -> set[str]:
    return {a.bundleIdentifier() for a in NSWorkspace.sharedWorkspace().runningApplications() if a.bundleIdentifier()}


def _automation_ok(bundle_id: str) -> bool:
    try:
        res = subprocess.run(
            ["osascript", "-e", f'tell application id "{bundle_id}" to return name'],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return res.returncode == 0


def report(cfg: LoggerConfig) -> dict[str, bool | None]:
    """Never launches an app: browsers that aren't running report None."""
    out: dict[str, bool | None] = {"accessibility": bool(AX.AXIsProcessTrusted())}
    running = _running_bundle_ids()
    for bid in cfg.browsers:
        out[f"automation:{bid}"] = _automation_ok(bid) if bid in running else None
    return out


def request_missing() -> None:
    """Show the Accessibility prompt. (Automation prompts appear on the first browser query.)"""
    AX.AXIsProcessTrustedWithOptions({AX.kAXTrustedCheckOptionPrompt: True})
