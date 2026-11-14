"""JSON-lines stdio server between the menu bar app and the DB.

One JSON object per line. Calls: {"id", "method", "params"} -> {"id", "result"} | {"id", "error"}.
Pushed: {"event", "data"}. All DB work happens on the thread that runs ``serve`` (the main thread).
"""

from __future__ import annotations

import json
import queue
import sys
import threading
import time
from collections.abc import Callable
from typing import Any, TextIO

from bridge.store import DB_ERRORS, DEFAULT_QUESTION, Store

PROTOCOL = 1
_POLL, _EOF = object(), object()


class _BridgeError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _db_state_for(exc: BaseException) -> str:
    from data.db import KeyMissingError, WrongKeyError

    if isinstance(exc, FileNotFoundError):
        return "no_db"
    if isinstance(exc, KeyMissingError):
        return "key_missing"
    if isinstance(exc, WrongKeyError):
        return "wrong_key"
    return "error"


def _log(msg: str) -> None:
    print(f"[bridge] {msg}", file=sys.stderr, flush=True)


class Server:
    def __init__(
        self,
        inp: TextIO,
        out: TextIO,
        open_store: Callable[[], Store],
        poll_s: float = 2.0,
        reopen_s: float = 10.0,
        now: Callable[[], float] = time.time,
    ):
        self.inp, self.out, self._open_store = inp, out, open_store
        self.poll_s, self.reopen_s, self.now = poll_s, reopen_s, now
        self.store: Store | None = None
        self.db_state = "error"
        self._last_open: float | None = None
        self._emitted: set[int] = set()
        self._ensure_store()

    # -- store lifecycle -------------------------------------------------
    def _ensure_store(self) -> bool:
        """Try to open the store (throttled). Returns True if db_state changed."""
        if self.store is not None:
            return False
        t = self.now()
        if self._last_open is not None and t - self._last_open < self.reopen_s:
            return False
        self._last_open = t
        before = self.db_state
        try:
            self.store = self._open_store()
            self.db_state = "ok"
        except Exception as e:  # FileNotFoundError, KeyMissingError, WrongKeyError, DB errors
            self.db_state = _db_state_for(e)
            if self.db_state != before:
                _log(f"store unavailable: {self.db_state}: {e}")
        return self.db_state != before

    def _need_store(self) -> Store:
        if self.store is None:
            raise _BridgeError(self.db_state, f"database unavailable ({self.db_state})")
        return self.store

    # -- output ----------------------------------------------------------
    def _write(self, obj: dict) -> None:
        self.out.write(json.dumps(obj) + "\n")
        self.out.flush()

    # -- calls -----------------------------------------------------------
    def handle_line(self, line: str) -> dict | None:
        line = line.strip()
        if not line:
            return None
        try:
            msg = json.loads(line)
            if not isinstance(msg, dict) or not isinstance(msg.get("method"), str):
                raise ValueError("expected an object with a string 'method'")
        except ValueError as e:
            return {"id": None, "error": {"code": "bad_request", "message": str(e)}}
        rid = msg.get("id")
        params = msg.get("params") or {}
        handler = getattr(self, f"_m_{msg['method']}", None)
        if handler is None:
            return {"id": rid, "error": {"code": "unknown_method", "message": msg["method"]}}
        try:
            return {"id": rid, "result": handler(params)}
        except _BridgeError as e:
            return {"id": rid, "error": {"code": e.code, "message": str(e)}}
        except (ValueError, TypeError, KeyError) as e:
            return {"id": rid, "error": {"code": "bad_request", "message": str(e)}}
        except DB_ERRORS as e:
            _log(f"db error in {msg['method']}: {e}")
            return {"id": rid, "error": {"code": "db_error", "message": str(e)}}

    def _m_hello(self, p: dict) -> dict:
        version = None
        if self.store is not None:
            try:
                row = self.store.conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
                version = row[0] if row else None
            except DB_ERRORS:
                pass
        return {"protocol": PROTOCOL, "db_state": self.db_state, "schema_version": version}

    def _m_status(self, p: dict) -> dict:
        if self.store is None:
            return {"db_state": self.db_state, "logger_running": False}
        return {"db_state": self.db_state, **self.store.status()}

    def _m_samples(self, p: dict) -> list:
        return self._need_store().samples(p.get("since"), int(p.get("limit", 200)))

    def _m_events(self, p: dict) -> list:
        return self._need_store().events(p.get("since"), int(p.get("limit", 100)))

    def _m_checkins(self, p: dict) -> list:
        return self._need_store().checkins(int(p.get("limit", 50)))

    def _m_timeline(self, p: dict) -> list:
        return self._need_store().timeline(str(p["day"]))

    def _m_readout(self, p: dict) -> dict | None:
        return self._need_store().readout()

    def _m_ack_delivered(self, p: dict) -> dict:
        self._need_store().ack_delivered(int(p["outbox_id"]))
        return {"ok": True}

    def _m_checkin_response(self, p: dict) -> dict:
        return self._respond(p, "checkin_id", Store.checkin_response)

    def _m_nudge_response(self, p: dict) -> dict:
        return self._respond(p, "nudge_id", Store.nudge_response)

    def _respond(self, p: dict, key: str, write: Callable[..., bool]) -> dict:
        response, responded_at = str(p["response"]), float(p["responded_at"])
        if p.get(key) is None:  # a test prompt: never touches label tables
            _log(f"test response: {key.split('_')[0]} -> {response}")
            return {"ok": True, "applied": False}
        return {"ok": True, "applied": write(self._need_store(), int(p[key]), response, responded_at)}

    # -- polling ---------------------------------------------------------
    def poll_once(self) -> list[dict]:
        events: list[dict] = []
        if self._ensure_store():
            events.append({"event": "status_changed", "data": {"db_state": self.db_state}})
        if self.store is not None:
            try:
                swept = self.store.sweep_expired()
                if swept:
                    _log(f"swept {swept} unanswered prompt(s) to dismissed")
                for row in self.store.pending_outbox():
                    if row["outbox_id"] in self._emitted:
                        continue
                    self._emitted.add(row["outbox_id"])
                    events.append(self._event_for(row))
            except DB_ERRORS as e:
                _log(f"db error while polling: {e}")
        for e in events:
            self._write(e)
        return events

    @staticmethod
    def _event_for(row: dict) -> dict:
        pl = row["payload"]
        base = {"outbox_id": row["outbox_id"]}
        if row["kind"] == "checkin":
            data = {**base, "checkin_id": row["ref_id"], "question": pl.get("question") or DEFAULT_QUESTION}
            name = "show_checkin"
        else:
            data = {**base, "nudge_id": row["ref_id"], "body": pl.get("body") or ""}
            name = "show_nudge"
        data.update(expires_at=row["expires_at"], test=bool(pl.get("test", False)))
        return {"event": name, "data": data}

    # -- main loop -------------------------------------------------------
    def serve(self) -> int:
        q: queue.Queue = queue.Queue()

        def reader() -> None:
            for line in self.inp:
                q.put(line)
            q.put(_EOF)

        def ticker() -> None:
            while True:
                time.sleep(self.poll_s)
                q.put(_POLL)

        threading.Thread(target=reader, daemon=True).start()
        threading.Thread(target=ticker, daemon=True).start()
        while True:
            item = q.get()
            if item is _EOF:
                return 0
            if item is _POLL:
                self.poll_once()
                continue
            resp = self.handle_line(item)
            if resp is not None:
                self._write(resp)
