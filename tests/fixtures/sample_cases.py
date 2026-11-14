"""Golden cases for sample assembly (privacy rules), shared with the Swift sampler.

`uv run python -m tests.fixtures.sample_cases` rewrites sample_cases.json from the Python reference
implementation (logger/sample.py:build_sample). The cases use the repo's default config
(logger/config.yaml + logger/financial.yaml) and ignore any config.local.yaml.
tests/logger/test_sample_fixtures.py fails if the JSON is stale.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from logger.config import load_config
from logger.sample import AppInfo, BrowserInfo, InputRecency, WindowInfo, build_sample

JSON_PATH = Path(__file__).with_name("sample_cases.json")
CHROME = {"bundle_id": "com.google.Chrome", "name": "Google Chrome"}
SAFARI = {"bundle_id": "com.apple.Safari", "name": "Safari"}
ARC = {"bundle_id": "company.thebrowser.Browser", "name": "Arc"}
VSCODE = {"bundle_id": "com.microsoft.VSCode", "name": "Code"}
ACTIVE = {"key_s": 1.0, "click_s": 10.0, "scroll_s": 2.5}

# (name, app, window, browser, recency, locked, idle_s, app_switches, dt[, media_playing])
CASES = [
    ("vscode_plain", VSCODE, {"title": "sampler.py — metal-focus", "fullscreen": False}, None, ACTIVE, False, 0.3, 0, 4.0),
    ("chrome_normal_tab", CHROME, {"title": "Video - YouTube", "fullscreen": False},
     {"url": "https://www.youtube.com/watch?v=abc&t=42#x", "private": False}, ACTIVE, False, 0.3, 1, 4.0),
    ("chrome_incognito", CHROME, {"title": "Secret", "fullscreen": False}, {"url": "https://x.com/a", "private": True}, ACTIVE, False, 0.3, 0, 4.0),
    ("safari_private_unknown_dropped", SAFARI, {"title": "Page", "fullscreen": False}, {"url": "https://x.com/a", "private": None}, ACTIVE, False, 0.3, 0, 4.0),
    ("arc_normal", ARC, {"title": "Docs", "fullscreen": True}, {"url": "https://docs.google.com/d/1", "private": False}, ACTIVE, False, 0.3, 0, 4.0),
    ("browser_probe_failed", CHROME, {"title": "Page", "fullscreen": False}, None, ACTIVE, False, 0.3, 0, 4.0),
    ("browser_no_window_url", CHROME, {"title": "New Tab", "fullscreen": False}, {"url": None, "private": False}, ACTIVE, False, 0.3, 0, 4.0),
    ("url_userinfo_port_upper", CHROME, {"title": "t", "fullscreen": False}, {"url": "http://user:pw@WWW.Host.IO:8080/x?q=1", "private": False}, ACTIVE, False, 0.3, 0, 4.0),
    ("file_url_no_domain", CHROME, {"title": "secret.pdf", "fullscreen": False}, {"url": "file:///Users/a/secret.pdf", "private": False}, ACTIVE, False, 0.3, 0, 4.0),
    ("chrome_internal_url", CHROME, {"title": "Settings", "fullscreen": False}, {"url": "chrome://settings/", "private": False}, ACTIVE, False, 0.3, 0, 4.0),
    ("excluded_password_manager", {"bundle_id": "com.1password.1password", "name": "1Password"}, {"title": "Vault", "fullscreen": False}, None, ACTIVE, False, 0.3, 1, 4.0),
    ("excluded_ibm_domain_suffix", CHROME, {"title": "repo", "fullscreen": False}, {"url": "https://github.ibm.com/org/repo", "private": False}, ACTIVE, False, 0.3, 0, 4.0),
    ("financial_listed_domain", CHROME, {"title": "Chase", "fullscreen": False}, {"url": "https://secure.chase.com/x", "private": False}, ACTIVE, False, 0.3, 0, 4.0),
    ("financial_domain_keyword", CHROME, {"title": "Welcome", "fullscreen": False}, {"url": "https://online.mysmalltownbank.com/", "private": False}, ACTIVE, False, 0.3, 0, 4.0),
    ("financial_fcu_keyword", CHROME, {"title": "Home", "fullscreen": False}, {"url": "https://home.lakeviewfcu.org/", "private": False}, ACTIVE, False, 0.3, 0, 4.0),
    ("financial_title_keyword", CHROME, {"title": "Account Summary - Online Banking", "fullscreen": False}, {"url": "https://example.com/x", "private": False}, ACTIVE, False, 0.3, 0, 4.0),
    ("financial_title_401k", CHROME, {"title": "Your 401(k) plan", "fullscreen": False}, {"url": "https://example.com/", "private": False}, ACTIVE, False, 0.3, 0, 4.0),
    ("financial_title_in_desktop_app", VSCODE, {"title": "balance.py — app", "fullscreen": False}, None, ACTIVE, False, 0.3, 0, 4.0),
    ("financial_app_bundle", {"bundle_id": "com.apple.stocks", "name": "Stocks"}, {"title": "Stocks", "fullscreen": False}, None, ACTIVE, False, 0.3, 0, 4.0),
    ("private_financial_tab_is_excluded", CHROME, {"title": "Chase", "fullscreen": False}, {"url": "https://secure.chase.com/", "private": True}, ACTIVE, False, 0.3, 0, 4.0),
    ("word_boundary_not_financial", CHROME, {"title": "Lo-fi beats - YouTube", "fullscreen": False}, {"url": "https://youtube.com/", "private": False}, ACTIVE, False, 0.3, 0, 4.0),
    ("non_browser_ignores_browser_info", VSCODE, {"title": "file.py", "fullscreen": False}, {"url": "https://x.com", "private": False}, ACTIVE, False, 0.3, 0, 4.0),
    ("locked", CHROME, {"title": "Page", "fullscreen": True}, {"url": "https://x.com", "private": False}, ACTIVE, True, 50.0, 0, 4.0),
    ("frontmost_unknown", None, None, None, ACTIVE, False, 0.3, 0, 4.0),
    ("window_probe_failed", VSCODE, None, None, ACTIVE, False, 0.3, 0, 4.0),
    ("recency_unmeasured", VSCODE, {"title": "x", "fullscreen": None}, None, None, False, None, 0, 4.0),
    ("recency_partial_and_boundary", VSCODE, {"title": "x", "fullscreen": False}, None,
     {"key_s": 4.0, "click_s": 3.999, "scroll_s": None}, False, 0.3, 0, 4.0),
    ("long_dt_after_gap_cap", VSCODE, {"title": "x", "fullscreen": False}, None, {"key_s": 4.5, "click_s": 4.5, "scroll_s": 4.5}, False, 4.5, 2, 4.6),
    ("video_playing_idle", CHROME, {"title": "Video - YouTube", "fullscreen": True},
     {"url": "https://www.youtube.com/watch?v=abc", "private": False}, None, False, 300.0, 0, 4.0, True),
    ("media_probe_says_no", VSCODE, {"title": "x", "fullscreen": False}, None, ACTIVE, False, 0.3, 0, 4.0, False),
    ("locked_drops_media", CHROME, {"title": "Video", "fullscreen": True}, {"url": "https://youtube.com/", "private": False},
     None, True, 300.0, 0, 4.0, True),
    ("excluded_keeps_media_flag", {"bundle_id": "com.1password.1password", "name": "1Password"},
     {"title": "Vault", "fullscreen": False}, None, None, False, 200.0, 0, 4.0, True),
]


def build_cases() -> list[dict]:
    cfg = load_config(local_path=Path("/nonexistent/config.local.yaml"))
    out = []
    for name, app, window, browser, recency, locked, idle_s, switches, dt, *rest in CASES:
        media = rest[0] if rest else None
        s = build_sample(
            ts=0.0, tz_offset_min=0, dt=dt,
            app=AppInfo(app["bundle_id"], app["name"], 1) if app else None,
            window=WindowInfo(**window) if window else None,
            browser=BrowserInfo(**browser) if browser else None,
            idle_s=idle_s,
            recency=InputRecency(**recency) if recency else None,
            locked=locked, app_switches=switches, cfg=cfg, media_playing=media,
        )
        expected = {k: v for k, v in dataclasses.asdict(s).items() if k not in ("ts", "tz_offset_min")}
        out.append({"name": name,
                    "inputs": {"app": app, "window": window, "browser": browser, "recency": recency,
                               "locked": locked, "idle_s": idle_s, "app_switches": switches, "dt": dt,
                               "media_playing": media},
                    "expected": expected})
    return out


def render() -> str:
    doc = {"_about": __doc__.strip().splitlines()[0],
           "config": "repo defaults: logger/config.yaml + logger/financial.yaml; no config.local.yaml",
           "cases": build_cases()}
    return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    JSON_PATH.write_text(render())
    print(f"wrote {JSON_PATH} ({len(CASES)} cases)")
