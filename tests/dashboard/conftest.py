import dataclasses

import pytest

from data.db import DbSink, connect, init_schema
from tests.data.test_db import FakeSample
from tests.data.test_windows import NOON, fill

KEY = "f" * 64
FIELDS = [f.name for f in dataclasses.fields(FakeSample)]


@pytest.fixture
def db_path(tmp_path):
    """20 min of coding, then 30 min of reddit, then an excluded (banking) sample."""
    path = tmp_path / "a.db"
    c = connect(path, KEY)
    init_schema(c)
    sink = DbSink(c)
    rows = fill(NOON - 1200, 300, title="main.py") + fill(NOON, 450, bundle_id="com.google.Chrome",
                                                         domain="reddit.com", title="<b>r/x</b>")
    for r in rows:
        sink.write_sample(FakeSample(**{k: r[k] for k in FIELDS}))
    sink.write_sample(FakeSample(ts=NOON + 1801, window_title=None, domain=None, excluded=1, app_name=None,
                                 bundle_id=None))
    c.execute("INSERT INTO events (ts, kind, detail) VALUES (?, 'logger_start', NULL)", (NOON - 1200,))
    c.execute("INSERT INTO checkins (asked_at, trigger, window_start, window_end, response, split) "
              "VALUES (?, 'gray_zone', ?, ?, 'Rabbit hole', 'train')", (NOON + 900, NOON + 300, NOON + 900))
    c.commit()
    c.close()
    return path


@pytest.fixture
def conn(db_path):
    c = connect(db_path, KEY)
    yield c
    c.close()
