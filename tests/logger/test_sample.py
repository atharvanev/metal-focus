import dataclasses
from dataclasses import replace

import pytest

from logger.config import load_config
from logger.sample import AppInfo, BrowserInfo, InputRecency, WindowInfo, build_sample

CHROME = AppInfo("com.google.Chrome", "Google Chrome", 10)
SAFARI = AppInfo("com.apple.Safari", "Safari", 11)
VSCODE = AppInfo("com.microsoft.VSCode", "Code", 12)
ONEPW = AppInfo("com.1password.1password", "1Password", 13)


@pytest.fixture(scope="module")
def cfg():
    return load_config()


def make(cfg, app=VSCODE, title="t", browser=None, locked=False, recency=InputRecency(1.0, 1.0, 1.0), dt=4.0):
    return build_sample(
        ts=1000.0, tz_offset_min=-480, dt=dt, app=app, window=WindowInfo(title, False),
        browser=browser, idle_s=0.5, recency=recency, locked=locked, app_switches=1, cfg=cfg,
    )


def test_normal_browser_tab_keeps_domain_and_title(cfg):
    s = make(cfg, CHROME, "Video - YouTube", BrowserInfo("https://www.youtube.com/watch?v=abc", False))
    assert (s.bundle_id, s.domain, s.window_title, s.private) == ("com.google.Chrome", "youtube.com", "Video - YouTube", 0)


def test_excluded_bundle_redacts_everything(cfg):
    s = make(cfg, ONEPW, "Vault")
    assert s.bundle_id is None and s.app_name is None and s.window_title is None
    assert s.excluded == 1
    assert s.key_active == 1


def test_excluded_domain_suffix(cfg):
    s = make(cfg, CHROME, "repo", BrowserInfo("https://github.ibm.com/org/repo", False))
    assert s.excluded == 1 and s.domain is None and s.window_title is None and s.bundle_id is None


def test_excluded_title_pattern(tmp_path):
    local = tmp_path / "l.yaml"
    local.write_text("exclude:\n  title_patterns: [secret-repo]\n")
    s = make(load_config(local_path=local), VSCODE, "main.py — Secret-Repo")
    assert s.excluded == 1 and s.window_title is None


def test_financial_title_in_browser_excluded(cfg):
    s = make(cfg, CHROME, "Account Summary - Online Banking", BrowserInfo("https://example.com/x", False))
    assert s.excluded == 1 and s.domain is None


def test_incognito_drops_title_and_domain(cfg):
    s = make(cfg, CHROME, "Secret", BrowserInfo("https://x.com/a", True))
    assert s.window_title is None and s.domain is None
    assert s.private == 1 and s.bundle_id == "com.google.Chrome"


def test_unknown_private_dropped_by_default(cfg):
    s = make(cfg, SAFARI, "Page", BrowserInfo("https://x.com/a", None))
    assert s.window_title is None and s.domain is None and s.private is None


def test_unknown_private_kept_when_configured(cfg):
    s = make(replace(cfg, unknown_private_policy="keep"), SAFARI, "Page", BrowserInfo("https://x.com/a", None))
    assert s.window_title == "Page" and s.domain == "x.com"


def test_browser_probe_failed_drops_title(cfg):
    s = make(cfg, CHROME, "Page", None)
    assert s.window_title is None and s.domain is None


def test_non_browser_has_no_domain_or_private(cfg):
    s = make(cfg, VSCODE, "file.py", BrowserInfo("https://x.com", False))
    assert s.domain is None and s.private is None and s.window_title == "file.py"


def test_locked_sample_has_no_app(cfg):
    s = make(cfg, CHROME, "Page", BrowserInfo("https://x.com", False), locked=True)
    assert s.bundle_id is None and s.window_title is None and s.domain is None and s.locked == 1


def test_activity_flags(cfg):
    s = make(cfg, recency=InputRecency(1.0, 10.0, None), dt=4.0)
    assert (s.key_active, s.click_active, s.scroll_active) == (1, 0, None)
    assert make(cfg, recency=None).key_active is None


def test_url_path_never_stored(cfg):
    s = make(cfg, CHROME, "Video", BrowserInfo("https://www.youtube.com/watch?v=abc", False))
    for v in dataclasses.asdict(s).values():
        assert "/watch" not in str(v) and "?v=" not in str(v)


def test_private_financial_tab_is_excluded_not_just_private(cfg):
    s = make(cfg, CHROME, "Chase", BrowserInfo("https://secure.chase.com/", True))
    assert s.excluded == 1 and s.bundle_id is None and s.private is None
