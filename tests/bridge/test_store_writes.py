import json

import pytest

from tests.bridge.conftest import NOW


def _checkin(conn, asked_at=NOW - 50):
    cur = conn.execute("INSERT INTO checkins (asked_at, trigger, split) VALUES (?, 'random', 'eval')", (asked_at,))
    return cur.lastrowid


def _nudge(conn, asked_at=NOW - 50):
    return conn.execute("INSERT INTO nudge_responses (nudge_id, asked_at) VALUES ('u1', ?)", (asked_at,)).lastrowid


def _outbox(conn, kind, ref_id, expires_at=NOW + 60, delivered_at=None, payload=None):
    return conn.execute(
        "INSERT INTO notify_outbox (created_at, kind, ref_id, payload_json, delivered_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (NOW - 1, kind, ref_id, json.dumps(payload or {}), delivered_at, expires_at),
    ).lastrowid


def _get(conn, table, rid):
    cur = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (rid,))
    return dict(zip([d[0] for d in cur.description], cur.fetchone()))


def test_pending_excludes_delivered_and_expired(conn, store):
    fresh = _outbox(conn, "checkin", None, payload={"question": "q"})
    _outbox(conn, "checkin", None, delivered_at=NOW - 5)
    _outbox(conn, "checkin", None, expires_at=NOW - 1)
    p = store.pending_outbox()
    assert [r["outbox_id"] for r in p] == [fresh]
    assert p[0]["payload"] == {"question": "q"} and p[0]["kind"] == "checkin" and p[0]["expires_at"] == NOW + 60


def test_ack_sets_asked_at_to_shown_time(conn, store):
    cid = _checkin(conn)
    oid = _outbox(conn, "checkin", cid)
    assert store.ack_delivered(oid) is True
    assert _get(conn, "checkins", cid)["asked_at"] == NOW
    assert _get(conn, "notify_outbox", oid)["delivered_at"] == NOW
    assert store.pending_outbox() == []


def test_ack_nudge_sets_asked_at(conn, store):
    nid = _nudge(conn)
    store.ack_delivered(_outbox(conn, "nudge", nid))
    assert _get(conn, "nudge_responses", nid)["asked_at"] == NOW


def test_checkin_response_writes(conn, store):
    cid = _checkin(conn)
    store.ack_delivered(_outbox(conn, "checkin", cid))
    assert store.checkin_response(cid, "Drifting", NOW + 7) is True
    row = _get(conn, "checkins", cid)
    assert row["response"] == "Drifting" and row["responded_at"] == NOW + 7 and row["split"] == "eval"


def test_second_response_not_applied(conn, store):
    cid = _checkin(conn)
    store.checkin_response(cid, "Drifting", NOW + 7)
    assert store.checkin_response(cid, "Working", NOW + 9) is False
    assert _get(conn, "checkins", cid)["response"] == "Drifting"


def test_invalid_response_rejected(conn, store):
    cid = _checkin(conn)
    with pytest.raises(ValueError):
        store.checkin_response(cid, "rabbit", NOW)
    with pytest.raises(ValueError):
        store.nudge_response(_nudge(conn), "Working", NOW)
    assert _get(conn, "checkins", cid)["response"] is None


def test_nudge_latency(conn, store):
    nid = _nudge(conn)
    store.ack_delivered(_outbox(conn, "nudge", nid))
    assert store.nudge_response(nid, "snooze", NOW + 4) is True
    row = _get(conn, "nudge_responses", nid)
    assert row["response"] == "snooze" and row["response_latency_s"] == 4


def test_sweep_marks_shown_unanswered_dismissed(conn, store):
    old = _checkin(conn)
    _outbox(conn, "checkin", old, expires_at=NOW - 31, delivered_at=NOW - 80)
    recent = _checkin(conn)
    _outbox(conn, "checkin", recent, expires_at=NOW - 10, delivered_at=NOW - 60)
    nid = _nudge(conn)
    _outbox(conn, "nudge", nid, expires_at=NOW - 40, delivered_at=NOW - 90)
    assert store.sweep_expired() == 2
    assert _get(conn, "checkins", old)["response"] == "dismissed"
    assert _get(conn, "checkins", old)["responded_at"] == NOW - 31
    assert _get(conn, "checkins", recent)["response"] is None
    assert _get(conn, "nudge_responses", nid)["response"] == "dismissed"
    assert store.sweep_expired() == 0


def test_sweep_ignores_never_shown(conn, store):
    cid = _checkin(conn)
    _outbox(conn, "checkin", cid, expires_at=NOW - 100)
    assert store.sweep_expired() == 0
    assert _get(conn, "checkins", cid)["response"] is None


def test_test_prompt_touches_no_label_tables(conn, store):
    oid = store.insert_test_prompt("checkin")
    p = store.pending_outbox()
    assert p[0]["outbox_id"] == oid and p[0]["ref_id"] is None and p[0]["payload"]["test"] is True
    assert p[0]["expires_at"] == NOW + 60
    store.insert_test_prompt("nudge")
    assert conn.execute("SELECT count(*) FROM checkins").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM nudge_responses").fetchone()[0] == 0
