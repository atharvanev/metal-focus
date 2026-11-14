import subprocess

import pytest

from logger.macos.browser import parse_reply, query_browser, script_for
from logger.sample import BrowserInfo


def runner(stdout="", returncode=0, exc=None):
    calls = []

    def run(args, **kw):
        calls.append((args, kw))
        if exc:
            raise exc
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")

    run.calls = calls
    return run


def test_chromium_incognito():
    assert parse_reply("chromium", "https://x.com/a\nincognito\n") == BrowserInfo("https://x.com/a", True)
    assert parse_reply("chromium", "https://x.com/a\nnormal\n") == BrowserInfo("https://x.com/a", False)
    assert parse_reply("chromium", "https://x.com/a\nweird\n").private is None


def test_arc_flag():
    assert parse_reply("arc", "https://a.b/\nfalse") == BrowserInfo("https://a.b/", False)
    assert parse_reply("arc", "https://a.b/\ntrue").private is True


def test_safari_private_unknown():
    assert parse_reply("safari", "https://a.b/\n") == BrowserInfo("https://a.b/", None)


def test_missing_value_reply():
    assert parse_reply("chromium", "missing value\nnormal").url is None
    assert parse_reply("chromium", "").url is None


def test_timeout_returns_none():
    run = runner(exc=subprocess.TimeoutExpired("osascript", 1.5))
    assert query_browser("com.google.Chrome", "chromium", 1.5, run=run) is None
    assert run.calls[0][1]["timeout"] == 1.5


def test_nonzero_exit_returns_none():
    assert query_browser("com.google.Chrome", "chromium", 1.5, run=runner(returncode=1)) is None


def test_oserror_returns_none():
    assert query_browser("com.google.Chrome", "chromium", 1.5, run=runner(exc=OSError("no osascript"))) is None


def test_query_parses_success():
    run = runner(stdout="https://x.com/a\nnormal\n")
    assert query_browser("com.brave.Browser", "chromium", 1.5, run=run) == BrowserInfo("https://x.com/a", False)
    assert run.calls[0][0][:2] == ["osascript", "-e"]


@pytest.mark.parametrize("kind", ["chromium", "arc", "safari"])
def test_scripts_address_by_bundle_id(kind):
    assert 'application id "com.brave.Browser"' in script_for(kind, "com.brave.Browser")
