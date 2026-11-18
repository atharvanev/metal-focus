import dataclasses
import re
import sqlite3

import pytest

from data.db import DbSink, KeyMissingError, WrongKeyError, connect, get_or_create_key, init_schema

SAMPLE_COLUMNS = {
    "id", "ts", "tz_offset_min", "dt", "bundle_id", "app_name", "window_title", "domain",
    "private", "excluded", "fullscreen", "locked", "idle_s", "app_switches",
    "key_active", "click_active", "scroll_active", "media_playing",  # 0/1 flags only
}


class FakeKeyStore:
    def __init__(self, key=None):
        self.key = key
        self.set_calls = 0

    def get(self):
        return self.key

    def set(self, key):
        self.key = key
        self.set_calls += 1


@dataclasses.dataclass(frozen=True)
class FakeSample:
    ts: float = 1000.0
    tz_offset_min: int = -480
    dt: float = 4.0
    bundle_id: str | None = "com.google.Chrome"
    app_name: str | None = "Google Chrome"
    window_title: str | None = "Very Distinctive Title 12345"
    domain: str | None = "youtube.com"
    private: int | None = 0
    excluded: int = 0
    fullscreen: int | None = 0
    locked: int = 0
    idle_s: float | None = 1.5
    app_switches: int = 2
    key_active: int | None = 1
    click_active: int | None = 0
    scroll_active: int | None = None


def _open(path, store):
    conn = connect(path, get_or_create_key(path, store))
    init_schema(conn)
    return conn


def test_first_run_creates_and_stores_key(tmp_path):
    store = FakeKeyStore()
    key = get_or_create_key(tmp_path / "a.db", store)
    assert re.fullmatch(r"[0-9a-f]{64}", key)
    assert store.get() == key


def test_second_run_reuses_key(tmp_path):
    store = FakeKeyStore()
    k1 = get_or_create_key(tmp_path / "a.db", store)
    k2 = get_or_create_key(tmp_path / "a.db", store)
    assert k1 == k2
    assert store.set_calls == 1


def test_db_exists_key_missing_raises(tmp_path):
    p = tmp_path / "a.db"
    _open(p, FakeKeyStore()).close()
    empty = FakeKeyStore()
    with pytest.raises(KeyMissingError):
        get_or_create_key(p, empty)
    assert empty.get() is None


def test_wrong_key_raises(tmp_path):
    p = tmp_path / "a.db"
    _open(p, FakeKeyStore("a" * 64)).close()
    with pytest.raises(WrongKeyError):
        connect(p, "b" * 64)


def test_file_is_encrypted(tmp_path):
    p = tmp_path / "a.db"
    conn = _open(p, FakeKeyStore())
    DbSink(conn).write_sample(FakeSample())
    conn.close()
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(p).execute("select * from samples").fetchall()
    for f in tmp_path.iterdir():
        assert b"Very Distinctive Title" not in f.read_bytes()


def test_db_file_mode_0600(tmp_path):
    p = tmp_path / "a.db"
    _open(p, FakeKeyStore()).close()
    assert (p.stat().st_mode & 0o777) == 0o600


def test_init_schema_idempotent(tmp_path):
    conn = _open(tmp_path / "a.db", FakeKeyStore())
    init_schema(conn)
    assert conn.execute("select value from meta where key='schema_version'").fetchone()[0] == "2"


def test_sink_roundtrip(tmp_path):
    conn = _open(tmp_path / "a.db", FakeKeyStore())
    sink = DbSink(conn)
    s = FakeSample()
    sink.write_sample(s)
    sink.write_event(1004.0, "gap", "3600.0s")
    cols = [c for c in dataclasses.asdict(s)]
    row = conn.execute(f"select {', '.join(cols)} from samples").fetchone()
    assert dict(zip(cols, row)) == dataclasses.asdict(s)
    assert conn.execute("select ts, kind, detail from events").fetchall() == [(1004.0, "gap", "3600.0s")]


def test_bad_event_kind_rejected(tmp_path):
    conn = _open(tmp_path / "a.db", FakeKeyStore())
    with pytest.raises(Exception):
        DbSink(conn).write_event(1.0, "keystroke")


def test_schema_has_no_content_columns(tmp_path):
    conn = _open(tmp_path / "a.db", FakeKeyStore())
    cols = {r[1] for r in conn.execute("pragma table_info(samples)")}
    assert cols == SAMPLE_COLUMNS


def test_open_existing_never_creates_db_or_key(tmp_path):
    from data.db import open_existing

    store = FakeKeyStore()
    with pytest.raises(FileNotFoundError):
        open_existing(tmp_path / "a.db", store)
    assert store.get() is None and not (tmp_path / "a.db").exists()


def test_open_existing_key_missing_raises(tmp_path):
    from data.db import open_existing

    p = tmp_path / "a.db"
    _open(p, FakeKeyStore()).close()
    with pytest.raises(KeyMissingError):
        open_existing(p, FakeKeyStore())


def test_open_existing_reads(tmp_path):
    from data.db import open_existing

    p, store = tmp_path / "a.db", FakeKeyStore()
    conn = _open(p, store)
    DbSink(conn).write_sample(FakeSample())
    conn.close()
    assert open_existing(p, store).execute("select count(*) from samples").fetchone()[0] == 1


V1_SAMPLES = """CREATE TABLE samples (id INTEGER PRIMARY KEY, ts REAL NOT NULL, tz_offset_min INTEGER NOT NULL,
  dt REAL NOT NULL, bundle_id TEXT, app_name TEXT, window_title TEXT, domain TEXT, private INTEGER,
  excluded INTEGER NOT NULL, fullscreen INTEGER, locked INTEGER NOT NULL, idle_s REAL, app_switches INTEGER NOT NULL,
  key_active INTEGER, click_active INTEGER, scroll_active INTEGER);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO meta VALUES ('schema_version', '1');
INSERT INTO samples (ts, tz_offset_min, dt, excluded, locked, app_switches) VALUES (1, 0, 4, 0, 0, 0);"""


def test_v1_db_is_migrated_by_writers_and_readers(tmp_path):
    from data.db import migrate, open_existing

    store = FakeKeyStore()
    path = tmp_path / "old.db"
    conn = connect(path, get_or_create_key(path, store))
    conn.executescript(V1_SAMPLES)
    conn.close()
    conn = open_existing(path, store)  # readers migrate too, so load_day can select the new column
    cols = [r[1] for r in conn.execute("PRAGMA table_info(samples)")]
    assert cols[-1] == "media_playing"
    assert conn.execute("SELECT media_playing FROM samples").fetchone() == (None,)
    assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "2"
    migrate(conn)  # idempotent
    init_schema(conn)
    assert [r[1] for r in conn.execute("PRAGMA table_info(samples)")].count("media_playing") == 1


def test_migrate_rebuilds_checkins_to_allow_away_return(tmp_path):
    from data.db import connect, migrate

    conn = connect(tmp_path / "old.db", "e" * 64)
    conn.execute("""CREATE TABLE checkins (
      id INTEGER PRIMARY KEY, asked_at REAL NOT NULL,
      trigger TEXT NOT NULL CHECK (trigger IN ('uncertain','gray_zone','ambiguous_app','random','daily_review')),
      window_start REAL, window_end REAL, model_top_state TEXT, model_top_prob REAL,
      response TEXT, responded_at REAL, split TEXT NOT NULL CHECK (split IN ('train','eval')))""")
    conn.execute("INSERT INTO checkins (asked_at, trigger, response, split) VALUES (1, 'random', 'Away', 'eval')")
    conn.commit()
    migrate(conn)
    migrate(conn)  # idempotent
    conn.execute("INSERT INTO checkins (asked_at, trigger, split) VALUES (2, 'away_return', 'train')")
    assert conn.execute("SELECT trigger, response FROM checkins ORDER BY id").fetchall() == [("random", "Away"), ("away_return", None)]
