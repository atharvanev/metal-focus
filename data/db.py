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


def init_schema(conn: sqlcipher3.Connection) -> None:
    conn.executescript(_SCHEMA.read_text())
    conn.commit()


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
