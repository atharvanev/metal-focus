import pytest

from labels.categorizer import Categorization, categorize, is_escalating, load_rules


@pytest.fixture(scope="module")
def rules():
    return load_rules()


@pytest.mark.parametrize("bundle_id, domain, title, expected", [
    ("com.microsoft.VSCode", None, "sampler.py — metal-focus", Categorization("Coding", False)),
    ("com.jetbrains.pycharm", None, "x", Categorization("Coding", False)),     # prefix rule
    ("com.googlecode.iterm2", None, "zsh", Categorization("Coding", False)),
    ("com.google.Chrome", "github.com", "PR #3", Categorization("Coding", False)),
    ("com.google.Chrome", "gist.github.com", "x", Categorization("Coding", False)),  # domain suffix
    ("com.google.Chrome", "docs.google.com", "Doc", Categorization("Docs", False)),
    ("com.tinyspeck.slackmacgap", None, "#general", Categorization("Chat", False)),
    ("com.hnc.Discord", None, "server", Categorization("Chat", True)),          # spec: Discord is low-confidence
    ("com.google.Chrome", "reddit.com", "r/python", Categorization("Social", False)),
    ("com.google.Chrome", "netflix.com", "Show", Categorization("Video", False)),
    ("com.google.Chrome", "youtube.com", "Funny cats - YouTube", Categorization("Video", True)),
    ("com.google.Chrome", "youtube.com", "MIT 6.006 Lecture 3: Sorting - YouTube", Categorization("Docs", False)),
    ("com.google.Chrome", "en.wikipedia.org", "Hidden semi-Markov model", Categorization("Docs", True)),
    ("com.google.Chrome", "someblog.example", "Post", Categorization("Other", True)),
    ("com.google.Chrome", None, None, Categorization("Other", True)),           # private/dropped tab
    ("com.apple.finder", None, "Downloads", Categorization("Other", True)),
    # neutral: neither work nor distraction, and known (no ambiguous-app check-ins)
    ("com.apple.SecurityAgent", None, "x", Categorization("Other", False)),
    ("com.apple.systempreferences", None, "Wi-Fi", Categorization("Other", False)),
    ("com.apple.ActivityMonitor", None, "x", Categorization("Other", False)),
    ("com.metalfocus.menubar", None, None, Categorization("Other", False)),
    ("com.google.Chrome", "www.google.com", "q - Google Search", Categorization("Other", False)),
    ("com.google.Chrome", "mail.google.com", "Inbox", Categorization("Chat", False)),  # own rule wins
    # Spotify: neutral per sample; windowing escalates long stretches to Social
    ("com.spotify.client", None, "Song", Categorization("Other", False)),
    ("com.google.Chrome", "open.spotify.com", "Song", Categorization("Other", False)),
])
def test_categorize(rules, bundle_id, domain, title, expected):
    assert categorize(bundle_id, domain, title, rules) == expected


def test_excluded_rows(rules):
    assert categorize(None, None, None, rules, excluded=True) == Categorization("Excluded", False)


def test_local_rules_extend_defaults(tmp_path):
    local = tmp_path / "l.yaml"
    local.write_text("domains:\n  Coding: [mycorp-ci.example]\n")
    r = load_rules(local_path=local)
    assert categorize("com.google.Chrome", "build.mycorp-ci.example", "x", r).category == "Coding"
    assert categorize("com.google.Chrome", "github.com", "x", r).category == "Coding"


def test_is_escalating(rules):
    assert is_escalating("com.spotify.client", rules)
    assert is_escalating("open.spotify.com", rules)
    for d in (None, "com.microsoft.VSCode", "youtube.com"):
        assert not is_escalating(d, rules)
    assert rules.escalate_category == "Social" and rules.escalate_after == 3


def test_local_rules_extend_neutral(tmp_path):
    local = tmp_path / "l.yaml"
    local.write_text("neutral:\n  bundle_ids: [com.apple.finder]\n")
    r = load_rules(local_path=local)
    assert categorize("com.apple.finder", None, "Downloads", r) == Categorization("Other", False)


def test_escalate_category_must_be_known(tmp_path):
    base = tmp_path / "c.yaml"
    base.write_text("escalate:\n  category: Music\n  after_windows: 3\n")
    with pytest.raises(ValueError):
        load_rules(path=base, local_path=tmp_path / "none.yaml")
