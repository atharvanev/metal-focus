import json

import pytest

from labels.categorizer import load_rules
from labels.report import build_report, main


@pytest.fixture(scope="module")
def rules():
    return load_rules()


def s(bundle_id, domain=None, title=None, dt=60.0, excluded=0, locked=0):
    return dict(ts=1000.0, dt=dt, bundle_id=bundle_id, domain=domain, window_title=title,
                excluded=excluded, locked=locked)


SAMPLES = [
    s("com.microsoft.VSCode", title="a.py", dt=600),
    s("com.google.Chrome", "reddit.com", "r/x", dt=120),
    s("com.apple.systempreferences", title="Wi-Fi", dt=60),               # neutral
    s("com.apple.finder", title="Downloads", dt=180),                      # unknown
    s("com.apple.finder", title="Desktop", dt=50),
    s("com.google.Chrome", "some-blog.dev", "Rust borrow checker", dt=240),  # unknown, more time
    s("com.google.Chrome", "youtube.com", "Funny cats", dt=90),            # known but unsure
    s(None, excluded=1, dt=300),
    s("com.microsoft.VSCode", title="locked", dt=500, locked=1),           # locked: not counted
]


def test_time_by_category_with_neutral_split(rules):
    r = build_report(SAMPLES, rules)
    assert r.total_s == 1640
    assert r.by_category["Coding"] == 600 and r.by_category["neutral"] == 60
    assert r.by_category["unknown"] == 470 and r.by_category["Excluded"] == 300
    assert r.by_category["Video"] == 90 and "Other" not in r.by_category


def test_unknown_and_unsure_ranked_by_time_with_top_title(rules):
    r = build_report(SAMPLES, rules)
    assert [(k.key, k.seconds, k.title) for k in r.unknown] == [
        ("some-blog.dev", 240, "Rust borrow checker"), ("com.apple.finder", 230, "Downloads")]
    assert [(k.key, k.category) for k in r.unsure] == [("youtube.com", "Video")]


def test_excluded_never_listed(rules):
    r = build_report(SAMPLES, rules)
    keys = {k.key for k in r.unknown + r.unsure} | {t for t, _ in r.refs}
    assert None not in keys and not any(k is None for k in keys)


def test_refs_are_high_confidence_categorized_only(rules):
    r = build_report(SAMPLES, rules)
    assert sorted(r.refs) == sorted([("com.microsoft.VSCode a.py", "Coding"), ("reddit.com r/x", "Social"),
                                     ("com.apple.systempreferences Wi-Fi", "neutral")])


class FakeConn:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params=()):
        cols = ("ts", "dt", "bundle_id", "domain", "window_title", "excluded", "locked")

        class Cur:
            description = [(c,) for c in cols]

            def fetchall(_):
                return [tuple(r[c] for c in cols) for r in self.rows]
        return Cur()


def test_empty_report_message(capsys):
    assert main(["--no-suggest"], conn=FakeConn([])) == 0
    assert "no samples" in capsys.readouterr().out


def test_json_no_titles(capsys):
    assert main(["--json", "--no-titles", "--no-suggest"], conn=FakeConn(SAMPLES)) == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert lines and all(x.get("title") is None for x in lines)
    assert {x["kind"] for x in lines} >= {"summary", "unknown", "unsure"}
