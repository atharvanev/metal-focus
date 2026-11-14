import io
import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

from bridge.server import Server
from bridge.store import Store
from data.db import KeyMissingError
from tests.bridge.conftest import NOW, add_sample

REPO = Path(__file__).resolve().parents[2]


def _server(open_store, clock, out=None):
    return Server(io.StringIO(""), out or io.StringIO(), open_store, now=lambda: clock["now"])


def _call(srv, method, params=None, id=1):
    return srv.handle_line(json.dumps({"id": id, "method": method, "params": params or {}}))


def test_hello_and_status_ok(conn, store, clock):
    add_sample(conn, NOW - 2)
    srv = _server(lambda: store, clock)
    h = _call(srv, "hello")
    assert h["id"] == 1 and h["result"]["protocol"] == 1 and h["result"]["db_state"] == "ok"
    assert h["result"]["schema_version"] == "1"
    s = _call(srv, "status", id=2)["result"]
    assert s["db_state"] == "ok" and s["logger_running"] is True and s["samples_today"] == 1


def test_unknown_method(store, clock):
    r = _call(_server(lambda: store, clock), "nope")
    assert r["error"]["code"] == "unknown_method" and r["id"] == 1


def test_malformed_line_keeps_running(store, clock):
    srv = _server(lambda: store, clock)
    r = srv.handle_line("not json")
    assert r["id"] is None and r["error"]["code"] == "bad_request"
    assert srv.handle_line("") is None
    assert _call(srv, "hello")["result"]["protocol"] == 1


def test_bad_params_are_bad_request(store, clock):
    r = _call(_server(lambda: store, clock), "checkin_response",
              {"checkin_id": 1, "response": "rabbit", "responded_at": NOW})
    assert r["error"]["code"] == "bad_request"


@pytest.mark.parametrize("exc,state", [
    (FileNotFoundError("x"), "no_db"), (KeyMissingError("x"), "key_missing"),
])
def test_no_db_serves_status(clock, exc, state):
    def boom():
        raise exc
    srv = _server(boom, clock)
    assert _call(srv, "hello")["result"]["db_state"] == state
    s = _call(srv, "status")["result"]
    assert s == {"db_state": state, "logger_running": False}
    assert _call(srv, "samples")["error"]["code"] == state


def test_store_open_never_sets_key(tmp_path):
    class FakeKeys:
        set_calls = 0
        def get(self):
            return None
        def set(self, key):
            FakeKeys.set_calls += 1
    db = tmp_path / "activity.db"
    db.write_bytes(b"")
    with pytest.raises(KeyMissingError):
        Store.open(db, FakeKeys())
    assert FakeKeys.set_calls == 0
    with pytest.raises(FileNotFoundError):
        Store.open(tmp_path / "missing.db", FakeKeys())


def test_recovers_when_db_appears(store, clock):
    calls = {"n": 0}
    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise FileNotFoundError("x")
        return store
    out = io.StringIO()
    srv = _server(flaky, clock, out)
    assert _call(srv, "hello")["result"]["db_state"] == "no_db"
    clock["now"] += 5
    srv.poll_once()
    assert calls["n"] == 1  # reopen throttled to every 10 s
    clock["now"] += 6
    events = srv.poll_once()
    assert {"event": "status_changed", "data": {"db_state": "ok"}} in events
    assert '"status_changed"' in out.getvalue()
    assert _call(srv, "samples")["result"] == []


def test_poll_emits_and_does_not_redeliver(store, clock):
    oid = store.insert_test_prompt("checkin")
    out = io.StringIO()
    srv = _server(lambda: store, clock, out)
    ev = srv.poll_once()
    assert len(ev) == 1 and ev[0]["event"] == "show_checkin"
    d = ev[0]["data"]
    assert d["outbox_id"] == oid and d["checkin_id"] is None and d["test"] is True
    assert d["question"] == "What were you doing the last 10 min?" and d["expires_at"] == NOW + 60
    assert srv.poll_once() == []  # emitted once per process until acked
    assert _call(srv, "ack_delivered", {"outbox_id": oid})["result"] == {"ok": True}
    assert srv.poll_once() == []
    assert json.loads(out.getvalue().splitlines()[0])["event"] == "show_checkin"


def test_show_nudge_event(conn, store, clock):
    nid = conn.execute("INSERT INTO nudge_responses (nudge_id, asked_at) VALUES ('u', ?)", (NOW,)).lastrowid
    conn.execute("INSERT INTO notify_outbox (created_at, kind, ref_id, payload_json, expires_at) "
                 "VALUES (?, 'nudge', ?, ?, ?)", (NOW, nid, json.dumps({"body": "YouTube -> Reddit"}), NOW + 60))
    ev = _server(lambda: store, clock).poll_once()
    assert ev[0] == {"event": "show_nudge", "data": {"outbox_id": 1, "nudge_id": nid, "body": "YouTube -> Reddit",
                                                     "expires_at": NOW + 60, "test": False}}


def test_responses_via_protocol(conn, store, clock):
    cid = conn.execute("INSERT INTO checkins (asked_at, trigger, split) VALUES (?, 'random', 'train')", (NOW,)).lastrowid
    srv = _server(lambda: store, clock)
    p = {"checkin_id": cid, "response": "Away", "shown_at": NOW, "responded_at": NOW + 3}
    assert _call(srv, "checkin_response", p)["result"] == {"ok": True, "applied": True}
    assert _call(srv, "checkin_response", p)["result"] == {"ok": True, "applied": False}
    test = {"checkin_id": None, "response": "Away", "shown_at": NOW, "responded_at": NOW + 3}
    assert _call(srv, "checkin_response", test)["result"] == {"ok": True, "applied": False}
    nt = {"nudge_id": None, "response": "snooze", "shown_at": NOW, "responded_at": NOW + 1}
    assert _call(srv, "nudge_response", nt)["result"] == {"ok": True, "applied": False}


def test_poll_runs_sweep(conn, store, clock):
    cid = conn.execute("INSERT INTO checkins (asked_at, trigger, split) VALUES (?, 'random', 'train')", (NOW,)).lastrowid
    conn.execute("INSERT INTO notify_outbox (created_at, kind, ref_id, delivered_at, expires_at) "
                 "VALUES (?, 'checkin', ?, ?, ?)", (NOW - 100, cid, NOW - 99, NOW - 40))
    _server(lambda: store, clock).poll_once()
    assert conn.execute("SELECT response FROM checkins").fetchone()[0] == "dismissed"


def test_db_error_becomes_error_response(store, clock, monkeypatch):
    def broken(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(store, "samples", broken)
    srv = _server(lambda: store, clock)
    assert _call(srv, "samples")["error"]["code"] == "db_error"
    assert _call(srv, "hello")["result"]["protocol"] == 1


def test_viewer_methods(conn, store, clock):
    add_sample(conn, NOW - 2)
    srv = _server(lambda: store, clock)
    assert len(_call(srv, "samples", {"limit": 5})["result"]) == 1
    assert _call(srv, "events")["result"] == []
    assert _call(srv, "checkins")["result"] == []
    assert _call(srv, "readout")["result"] is None
    from datetime import datetime
    day = datetime.fromtimestamp(NOW).strftime("%Y-%m-%d")
    assert _call(srv, "timeline", {"day": day})["result"][0]["label"] == "Code"


def test_serve_subprocess_smoke(tmp_path):
    env = {**os.environ, "METAL_FOCUS_HOME": str(tmp_path)}
    env.pop("VIRTUAL_ENV", None)
    p = subprocess.run(["uv", "run", "python", "-m", "bridge"], input='{"id":1,"method":"hello"}\n',
                       capture_output=True, text=True, cwd=REPO, env=env, timeout=30)
    assert p.returncode == 0, p.stderr
    lines = [json.loads(l) for l in p.stdout.splitlines()]
    assert lines[0]["id"] == 1 and lines[0]["result"]["db_state"] == "no_db"
