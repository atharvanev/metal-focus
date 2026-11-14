import sqlite3
from pathlib import Path

import pytest

from bridge.store import Store

NOW = 1_763_000_000.0
_LOGGER_SCHEMA = Path(__file__).resolve().parents[2] / "data" / "schema.sql"

SAMPLE_DEFAULTS = dict(
    tz_offset_min=0, dt=4.0, bundle_id="com.microsoft.VSCode", app_name="Code", window_title="x.py",
    domain=None, private=None, excluded=0, fullscreen=0, locked=0, idle_s=0.0, app_switches=0,
    key_active=1, click_active=0, scroll_active=0,
)


def add_sample(conn, ts, **over):
    row = {**SAMPLE_DEFAULTS, **over, "ts": ts}
    cols = ", ".join(row)
    conn.execute(f"INSERT INTO samples ({cols}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
    conn.commit()


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(_LOGGER_SCHEMA.read_text())
    return c


@pytest.fixture
def clock():
    return {"now": NOW}


@pytest.fixture
def store(conn, clock):
    return Store(conn, now=lambda: clock["now"])
