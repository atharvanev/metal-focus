import re
from pathlib import Path

import pytest

from data.db import DbSink, connect, get_or_create_key, init_schema
from logger.__main__ import main
from tests.data.test_db import FakeKeyStore, FakeSample


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("METAL_FOCUS_HOME", str(tmp_path))
    return tmp_path


def test_status_reports_counts(home, capsys):
    store = FakeKeyStore()
    conn = connect(home / "activity.db", get_or_create_key(home / "activity.db", store))
    init_schema(conn)
    sink = DbSink(conn)
    for i in range(3):
        sink.write_sample(FakeSample(ts=1000.0 + 4 * i))
    sink.write_event(1000.0, "logger_start")
    conn.close()
    assert main(["status"], key_store=store) == 0
    out = capsys.readouterr().out
    assert "samples: 3" in out and "logger_start" in out


def test_status_without_db_does_not_create_key(home, capsys):
    store = FakeKeyStore()
    assert main(["status"], key_store=store) == 1
    assert store.get() is None
    assert not (home / "activity.db").exists()


def test_run_refuses_when_key_missing(home, capsys):
    store = FakeKeyStore()
    conn = connect(home / "activity.db", get_or_create_key(home / "activity.db", store))
    init_schema(conn)
    conn.close()
    empty = FakeKeyStore()
    assert main(["run"], key_store=empty) == 2
    assert empty.get() is None
    assert "refusing to create a new key" in capsys.readouterr().err


FORBIDDEN = ["CGEventTapCreate", "addGlobalMonitorForEventsMatchingMask", "addLocalMonitorForEventsMatchingMask",
             "CGRequestListenEventAccess", "IOHIDManager", "CGWindowListCreateImage", "keycode", "kCGKeyboardEventKeycode"]


def test_no_input_hooks_in_source():
    root = Path(__file__).resolve().parents[2] / "logger"
    for f in root.rglob("*.py"):
        text = f.read_text()
        for bad in FORBIDDEN:
            assert not re.search(re.escape(bad), text, re.IGNORECASE), f"{bad} found in {f}"
