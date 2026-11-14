import dataclasses
import json

import pytest

from data.db import DbSink, connect, init_schema
from labels.checkins import load_checkin_config, load_eval, tick
from tests.data.test_db import FakeSample
from tests.data.test_windows import DAY, NOON, fill

CFG = load_checkin_config()
FIELDS = [f.name for f in dataclasses.fields(FakeSample)]


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "a.db", "f" * 64)
    init_schema(c)
    sink = DbSink(c)
    rows = fill(NOON - 240, 60) + fill(NOON, 6 * 30, bundle_id="com.google.Chrome", domain="reddit.com", title="r/x")
    for r in rows:
        sink.write_sample(FakeSample(**{k: r[k] for k in FIELDS}))
    return c


NOW = NOON + 6 * 120 + 1  # six reddit windows completed: 12 min into a distraction session


def test_tick_inserts_checkin_and_outbox_row(conn):
    req = tick(conn, now=NOW, cfg=CFG)
    assert req.trigger == "gray_zone"
    ck = conn.execute("SELECT id, asked_at, trigger, window_start, window_end, response, split FROM checkins").fetchall()
    assert len(ck) == 1
    cid, asked_at, trigger, ws, we, response, split = ck[0]
    assert (asked_at, trigger, ws, we, response, split) == (NOW, "gray_zone", NOON + 120, NOON + 720, None, "train")
    ob = conn.execute("SELECT created_at, kind, ref_id, payload_json, delivered_at, expires_at FROM notify_outbox").fetchall()
    assert len(ob) == 1
    created, kind, ref, payload, delivered, expires = ob[0]
    assert (created, kind, ref, delivered, expires) == (NOW, "checkin", cid, None, NOW + 60)
    assert json.loads(payload)["question"] == "What were you doing the last 10 min?"


def test_second_tick_respects_min_gap(conn):
    assert tick(conn, now=NOW, cfg=CFG) is not None
    assert tick(conn, now=NOW + 120, cfg=CFG) is None
    assert conn.execute("SELECT count(*) FROM checkins").fetchone()[0] == 1


def test_tick_on_empty_db_does_nothing(tmp_path):
    c = connect(tmp_path / "b.db", "f" * 64)
    init_schema(c)
    assert tick(c, now=NOW, cfg=CFG) is None


def test_tick_uses_hsmm_readouts_for_uncertain(conn):
    from hsmm.runner import ensure_schema

    ensure_schema(conn)
    for i in range(1, 3):
        ws = NOON + 120 * (6 - i)
        r = {"risk": 0.4, "p_state": [0.5, 0.2, 0.2, 0.1], "top_state": 0, "top_prob": 0.5, "away": False}
        conn.execute("INSERT INTO readouts VALUES (?, 'hsmm', ?, ?, 'p', ?)", (ws, DAY, NOW, json.dumps(r)))
    conn.commit()
    req = tick(conn, now=NOW, cfg=CFG)
    assert (req.trigger, req.model_top_state, req.model_top_prob) == ("uncertain", "Working", 0.5)


def test_load_eval_returns_answered_eval_checkins_only(conn):
    q = ("INSERT INTO checkins (asked_at, trigger, window_start, window_end, response, responded_at, split) "
         "VALUES (?, ?, ?, ?, ?, ?, ?)")
    conn.execute(q, (NOON, "random", NOON - 600, NOON, "Rabbit hole", NOON + 5, "eval"))
    conn.execute(q, (NOON + 10, "random", NOON - 600, NOON, "skip", NOON + 15, "eval"))
    conn.execute(q, (NOON + 20, "random", NOON - 600, NOON, None, None, "eval"))
    conn.execute(q, (NOON + 30, "gray_zone", NOON - 600, NOON, "Drifting", NOON + 35, "train"))
    conn.execute(q, (NOON + 86400, "random", NOON + 86000, NOON + 86400, "Away", NOON + 86405, "eval"))
    conn.commit()
    ev = load_eval(conn, DAY, DAY)
    assert [(e.response, e.trigger, e.window_start, e.window_end) for e in ev] == [("Rabbit hole", "random", NOON - 600, NOON)]
    assert len(load_eval(conn, DAY, "2026-11-14")) == 2
