import pytest

from labels.categorizer import Categorization, categorize, load_rules


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
