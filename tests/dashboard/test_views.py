from dashboard import views
from tests.data.test_windows import DAY, NOON

NOW = NOON + 4000


def test_list_days(conn):
    assert views.list_days(conn) == [DAY]


def test_day_view_windows_labels_and_totals(conn):
    d = views.day_view(conn, DAY, now=NOW)
    ws = d["windows"]
    assert ws, "expected completed windows"
    cats = {w["category"] for w in ws}
    assert {"Coding", "Social"} <= cats
    states = [w["state"] for w in ws]
    assert "Working" in states and "Rabbit hole" in states
    # the check-in covering NOON+300..NOON+900 overrides the auto-label
    ck = [w for w in ws if NOON + 300 <= w["start"] < NOON + 900]
    assert ck and all(w["source"] == "checkin" for w in ck)
    t = d["totals"]
    assert t["rabbit_holes"] == 1
    assert (t["checkins_asked"], t["checkins_answered"]) == (1, 1)
    assert t["samples"] == 751 and t["logged_min"] == round(751 * 4 / 60, 1)
    assert d["has_readouts"] is False and d["nudges"] == []


def test_day_view_tops_respect_exclusion(conn):
    d = views.day_view(conn, DAY, now=NOW)
    assert d["top_domains"][0]["name"] == "reddit.com"
    assert "Excluded" in [a["name"] for a in d["top_apps"]]
    assert all(t["name"] is not None for t in d["top_titles"])
    assert {e["kind"] for e in d["events"]} == {"logger_start"}


def test_day_view_reads_readouts_when_present(conn):
    from hsmm.runner import ensure_schema

    ensure_schema(conn)
    ws = views.day_view(conn, DAY, now=NOW)["windows"]
    conn.execute("INSERT INTO readouts VALUES (?, 'hsmm', ?, 0, 'sha256:x', '{\"risk\": 0.7}')", (ws[0]["start"], DAY))
    d = views.day_view(conn, DAY, now=NOW)
    assert d["has_readouts"] and d["windows"][0]["risk"] == 0.7 and d["windows"][1]["risk"] is None


def test_empty_day(conn):
    d = views.day_view(conn, "2020-01-01", now=NOW)
    assert d["windows"] == [] and d["totals"]["samples"] == 0


def test_live(conn):
    lv = views.live(conn, limit=5, now=NOON + 1805)
    assert len(lv["samples"]) == 5 and lv["running"] is True
    assert lv["samples"][0]["excluded"] == 1 and lv["samples"][0]["window_title"] is None
    assert "media_playing" in lv["samples"][0]
    assert views.live(conn, now=NOON + 9999)["running"] is False
