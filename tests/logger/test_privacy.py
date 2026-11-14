import pytest

from logger.config import load_config
from logger.privacy import domain_matches, is_excluded, sanitize_url


@pytest.mark.parametrize("url, expected", [
    ("https://www.YouTube.com/watch?v=abc#t=1", "youtube.com"),
    ("http://user:pw@host.io:8080/x?q=1", "host.io"),
    ("file:///Users/a/secret.pdf", None),
    ("chrome://newtab/", None),
    ("about:blank", None),
    (None, None),
    ("not a url", None),
    ("", None),
])
def test_sanitize_url(url, expected):
    assert sanitize_url(url) == expected


@pytest.mark.parametrize("domain, suffix, expected", [
    ("chase.com", "chase.com", True),
    ("secure.chase.com", "chase.com", True),
    ("notchase.com", "chase.com", False),
    ("chase.com.evil.io", "chase.com", False),
])
def test_domain_matches(domain, suffix, expected):
    assert domain_matches(domain, suffix) is expected


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.mark.parametrize("domain, title", [
    ("secure.chase.com", "Chase"),
    ("online.mysmalltownbank.com", "Welcome"),
    ("home.lakeviewfcu.org", "Home"),
    ("example.com", "Account Summary - Online Banking"),
    ("example.com", "Your 401(k) plan"),
])
def test_financial_sites_excluded_automatically(cfg, domain, title):
    assert is_excluded("com.google.Chrome", domain, title, cfg)


def test_ordinary_site_not_excluded(cfg):
    assert not is_excluded("com.google.Chrome", "youtube.com", "Lo-fi beats - YouTube", cfg)


def test_excluded_bundle_id(cfg):
    assert is_excluded("com.1password.1password", None, None, cfg)
