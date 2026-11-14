import pytest

from labels.config import load_labels_config
from labels.merge import load_training_labels, merge_labels
from tests.labels.test_autolabel import wins

T0 = 1000.0  # window i starts at T0 + 120 i


def w(i):
    return T0 + 120 * i


@pytest.fixture(scope="module")
def cfg():
    return load_labels_config()


def test_auto_only(cfg):
    ws = wins("WDDW")
    out = merge_labels(ws, ["Working", "Short break", "Short break", "Working"], [], [], cfg)
    assert out == [("Working", "auto"), ("Short break", "auto"), ("Short break", "auto"), ("Working", "auto")]


def test_none_has_no_source(cfg):
    assert merge_labels(wins("D"), [None], [], [], cfg) == [(None, None)]


def test_nudge_back_to_work_labels_session_drift_then_rabbit(cfg):
    ws = wins("WDDDDW")
    nudges = [dict(response="back_to_work", window_start=w(1), window_end=w(5))]
    out = merge_labels(ws, ["Working", None, None, None, None, "Working"], nudges, [], cfg)
    assert [s for s, _ in out] == ["Working", "Drifting", "Rabbit hole", "Rabbit hole", "Rabbit hole", "Working"]
    assert [src for _, src in out][1:5] == ["nudge"] * 4


def test_nudge_on_break_is_short_break_and_other_responses_ignored(cfg):
    ws = wins("WDDW")
    auto = ["Working", None, None, "Working"]
    out = merge_labels(ws, auto, [dict(response="on_break", window_start=w(1), window_end=w(3))], [], cfg)
    assert out[1] == ("Short break", "nudge")
    for resp in ("snooze", "dismissed", None):
        assert merge_labels(ws, auto, [dict(response=resp, window_start=w(1), window_end=w(3))], [], cfg)[1] == (None, None)


def test_checkin_beats_nudge_beats_auto(cfg):
    ws = wins("WDDDDW")
    auto = ["Working", "Short break", "Short break", "Short break", "Short break", "Working"]
    nudges = [dict(response="back_to_work", window_start=w(1), window_end=w(5))]
    checkins = [dict(response="Drifting", window_start=w(3), window_end=w(5), split="train")]
    out = merge_labels(ws, auto, nudges, checkins, cfg)
    assert out[1][1] == "nudge" and out[3] == ("Drifting", "checkin") and out[4] == ("Drifting", "checkin")


def test_eval_checkins_never_reach_training(cfg):
    ws = wins("WDW")
    out = merge_labels(ws, ["Working", None, "Working"], [],
                       [dict(response="Rabbit hole", window_start=w(1), window_end=w(2), split="eval")], cfg)
    assert out[1] == (None, None)


@pytest.mark.parametrize("resp", ["skip", "dismissed", None])
def test_non_label_checkin_responses_ignored(cfg, resp):
    out = merge_labels(wins("WDW"), ["Working", "Short break", "Working"], [],
                       [dict(response=resp, window_start=w(1), window_end=w(2), split="train")], cfg)
    assert out[1] == ("Short break", "auto")


def test_window_end_is_exclusive(cfg):
    out = merge_labels(wins("WWW"), ["Working"] * 3, [],
                       [dict(response="Away", window_start=w(0), window_end=w(1), split="train")], cfg)
    assert [s for s, _ in out] == ["Away", "Working", "Working"]


def test_load_training_labels_from_db(tmp_path, cfg):
    import dataclasses

    from data.db import DbSink, connect, init_schema
    from tests.data.test_db import FakeSample
    from tests.data.test_windows import DAY, NOON, fill

    conn = connect(tmp_path / "a.db", "e" * 64)
    init_schema(conn)
    sink = DbSink(conn)
    fields = [f.name for f in dataclasses.fields(FakeSample)]
    for row in fill(NOON, 60) + fill(NOON + 240, 90, bundle_id="com.google.Chrome", domain="reddit.com") + fill(NOON + 600, 60):
        sink.write_sample(FakeSample(**{k: row[k] for k in fields}))
    conn.execute("INSERT INTO checkins (asked_at, trigger, window_start, window_end, response, responded_at, split) "
                 "VALUES (?, 'random', ?, ?, 'Rabbit hole', ?, 'eval')", (NOON + 700, NOON + 240, NOON + 600, NOON + 710))
    conn.execute("INSERT INTO checkins (asked_at, trigger, window_start, window_end, response, responded_at, split) "
                 "VALUES (?, 'gray_zone', ?, ?, 'Drifting', ?, 'train')", (NOON + 700, NOON + 240, NOON + 360, NOON + 710))
    conn.commit()
    out = load_training_labels(conn, DAY, now=NOON + 3600)
    assert out == [("Working", "auto"), ("Working", "auto"), ("Drifting", "checkin"),
                   ("Short break", "auto"), ("Short break", "auto"), ("Working", "auto"), ("Working", "auto")]
