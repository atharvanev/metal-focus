"""Encrypted (SQLCipher) local store for raw logger samples and events.

The key is a random 256-bit value kept in the macOS Keychain. It is created exactly once, on the
first run, and only when no database file exists yet: if the file exists but the key is gone we
refuse to continue, because a new key would make every past row unreadable.
"""

from __future__ import annotations

import dataclasses
import re
import secrets
from pathlib import Path
from typing import Any, Protocol

import sqlcipher3

KEYRING_SERVICE = "metal-focus"
KEYRING_ACCOUNT = "sqlcipher-key"
_SCHEMA = Path(__file__).with_name("schema.sql")
_HEX_KEY = re.compile(r"[0-9a-f]{64}")


class KeyMissingError(RuntimeError):
    """The database exists but its key is not in the key store."""


class WrongKeyError(RuntimeError):
    """The key does not decrypt the database (or the file is not a database)."""


class KeyStore(Protocol):
    def get(self) -> str | None: ...
    def set(self, key: str) -> None: ...


class KeyringStore:
    """Key store backed by the macOS Keychain via ``keyring``."""

    def get(self) -> str | None:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)

    def set(self, key: str) -> None:
        import keyring

        keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, key)


def get_or_create_key(db_path: Path, store: KeyStore) -> str:
    key = store.get()
    if key:
        return key
    if Path(db_path).exists():
        raise KeyMissingError(
            f"{db_path} exists but its key is missing from the key store; refusing to create a new key."
        )
    key = secrets.token_hex(32)
    store.set(key)
    return key


def connect(db_path: Path, key: str) -> sqlcipher3.Connection:
    if not _HEX_KEY.fullmatch(key):
        raise WrongKeyError("key must be 64 lowercase hex characters")
    conn = sqlcipher3.connect(str(db_path))
    conn.execute(f"PRAGMA key = \"x'{key}'\"")
    try:
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlcipher3.DatabaseError as e:
        conn.close()
        raise WrongKeyError(f"cannot open {db_path}: {e}") from e
    for p in (Path(db_path), Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        if p.exists():
            p.chmod(0o600)
    return conn


SCHEMA_VERSION = "2"  # the checkins rebuild (_rebuild_checkins) is detected from the table, not the version
# Columns added after v1, as (table, column, type). SQLite has no ADD COLUMN IF NOT EXISTS, so every
# opener (Python here, the Swift app in Database.applySchema) adds missing ones. Keep both lists equal.
ADDED_COLUMNS = (("samples", "media_playing", "INTEGER"),)


def migrate(conn: sqlcipher3.Connection) -> None:
    """Bring an older DB up to SCHEMA_VERSION. Idempotent; safe if another process migrates concurrently."""
    for table, col, typ in ADDED_COLUMNS:
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if have and col not in have:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            except sqlcipher3.OperationalError as e:
                if "duplicate column" not in str(e):
                    raise
    _rebuild_checkins(conn)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone():
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
    conn.commit()


def _rebuild_checkins(conn: sqlcipher3.Connection) -> None:
    """The checkins trigger CHECK gained 'away_return'. SQLite can't alter a CHECK, so copy the
    table into one created from schema.sql. Only Python inserts check-ins, so the app needs no twin."""
    def stale() -> bool:
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='checkins'").fetchone()
        return bool(row) and "'away_return'" not in row[0]

    if not stale():
        return
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")  # other jobs may migrate at the same moment: re-check under the lock
    try:
        if stale():
            ddl = re.search(r"CREATE TABLE IF NOT EXISTS checkins \(.*?\);", _SCHEMA.read_text(), re.S).group(0)
            conn.execute("ALTER TABLE checkins RENAME TO checkins_v2")
            conn.execute(ddl)
            conn.execute("INSERT INTO checkins SELECT * FROM checkins_v2")
            conn.execute("DROP TABLE checkins_v2")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def init_schema(conn: sqlcipher3.Connection) -> None:
    conn.executescript(_SCHEMA.read_text())
    migrate(conn)


class DbSink:
    """Writes samples (any dataclass with the ``samples`` columns) and events; commits each call."""

    def __init__(self, conn: sqlcipher3.Connection):
        self.conn = conn

    def write_sample(self, sample: Any) -> None:
        row = dataclasses.asdict(sample)
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        self.conn.execute(f"INSERT INTO samples ({cols}) VALUES ({marks})", tuple(row.values()))
        self.conn.commit()

    def write_event(self, ts: float, kind: str, detail: str | None = None) -> None:
        self.conn.execute("INSERT INTO events (ts, kind, detail) VALUES (?, ?, ?)", (ts, kind, detail))
        self.conn.commit()


def open_existing(db_path: Path | None = None, store: KeyStore | None = None) -> sqlcipher3.Connection:
    """Open the logger's database for a reader (HSMM runner, dashboard). Never creates the DB or a key;
    adds columns from newer schema versions (``migrate``) so readers can query them.

    Raises FileNotFoundError if the DB doesn't exist yet, KeyMissingError if its key is gone, and
    WrongKeyError if the key doesn't decrypt it. Defaults to ``common.paths.db_path()`` and the Keychain.
    """
    if db_path is None:
        from common.paths import db_path as default_db_path

        db_path = default_db_path()
    if not Path(db_path).exists():
        raise FileNotFoundError(f"{db_path} does not exist yet (is the logger running?)")
    key = (store or KeyringStore()).get()
    if not key:
        raise KeyMissingError(f"{db_path} exists but its key is missing from the key store.")
    conn = connect(Path(db_path), key)
    migrate(conn)
    return conn
