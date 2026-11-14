"""Raw observation types and the privacy-applying sample builder."""

from __future__ import annotations

from dataclasses import dataclass

from logger.config import LoggerConfig
from logger.privacy import is_excluded, sanitize_url


@dataclass(frozen=True)
class AppInfo:
    bundle_id: str | None
    name: str | None
    pid: int


@dataclass(frozen=True)
class WindowInfo:
    title: str | None
    fullscreen: bool | None


@dataclass(frozen=True)
class BrowserInfo:
    url: str | None
    private: bool | None


@dataclass(frozen=True)
class InputRecency:
    """Seconds since the last key press / click / scroll (system-wide; no content)."""

    key_s: float | None
    click_s: float | None
    scroll_s: float | None


@dataclass(frozen=True)
class Sample:
    """One row of the ``samples`` table (minus ``id``)."""

    ts: float
    tz_offset_min: int
    dt: float
    bundle_id: str | None
    app_name: str | None
    window_title: str | None
    domain: str | None
    private: int | None
    excluded: int
    fullscreen: int | None
    locked: int
    idle_s: float | None
    app_switches: int
    key_active: int | None
    click_active: int | None
    scroll_active: int | None


def _flag(v: bool | None) -> int | None:
    return None if v is None else int(v)


def _active(seconds_since: float | None, dt: float) -> int | None:
    return None if seconds_since is None else int(seconds_since < dt)


def build_sample(
    *,
    ts: float,
    tz_offset_min: int,
    dt: float,
    app: AppInfo | None,
    window: WindowInfo | None,
    browser: BrowserInfo | None,
    idle_s: float | None,
    recency: InputRecency | None,
    locked: bool,
    app_switches: int,
    cfg: LoggerConfig,
) -> Sample:
    bundle_id = app.bundle_id if app else None
    app_name = app.name if app else None
    title = window.title if window else None
    fullscreen = _flag(window.fullscreen) if window else None
    domain: str | None = None
    private: int | None = None
    excluded = 0

    is_browser = bundle_id in cfg.browsers
    if is_browser and browser:
        domain = sanitize_url(browser.url)

    if locked:
        bundle_id = app_name = title = domain = None
        fullscreen = None
    elif is_excluded(bundle_id, domain, title, cfg):
        # Checked on the raw domain/title, so a private bank tab is still "excluded".
        bundle_id = app_name = title = domain = None
        excluded = 1
    elif is_browser:
        priv = browser.private if browser else None
        private = _flag(priv)
        if priv is True or (priv is None and cfg.unknown_private_policy == "drop"):
            title = domain = None

    r = recency
    return Sample(
        ts=ts,
        tz_offset_min=tz_offset_min,
        dt=dt,
        bundle_id=bundle_id,
        app_name=app_name,
        window_title=title,
        domain=domain,
        private=private,
        excluded=excluded,
        fullscreen=fullscreen,
        locked=int(locked),
        idle_s=idle_s,
        app_switches=app_switches,
        key_active=_active(r.key_s, dt) if r else None,
        click_active=_active(r.click_s, dt) if r else None,
        scroll_active=_active(r.scroll_s, dt) if r else None,
    )
