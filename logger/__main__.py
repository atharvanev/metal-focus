"""CLI: uv run python -m logger {run,check,status}."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime

from common.paths import db_path
from data.db import DbSink, KeyMissingError, KeyringStore, KeyStore, connect, get_or_create_key, init_schema
from logger.config import load_config

KEY_MISSING_MSG = "Database exists but its key is missing from Keychain; refusing to create a new key."


def _fmt(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "-"


def cmd_run(store: KeyStore) -> int:
    cfg = load_config()
    path = db_path()
    try:
        key = get_or_create_key(path, store)
    except KeyMissingError:
        print(KEY_MISSING_MSG, file=sys.stderr)
        return 2
    conn = connect(path, key)
    init_schema(conn)
    from logger.macos.runloop import run

    run(cfg, DbSink(conn))
    return 0


def cmd_status(store: KeyStore) -> int:
    path = db_path()
    if not path.exists():
        print(f"no database yet at {path}")
        return 1
    key = store.get()
    if not key:
        print(KEY_MISSING_MSG, file=sys.stderr)
        return 2
    conn = connect(path, key)
    n, last = conn.execute("SELECT count(*), max(ts) FROM samples").fetchone()
    recent = conn.execute("SELECT count(*) FROM samples WHERE ts > ?", (time.time() - 600,)).fetchone()[0]
    events = conn.execute("SELECT ts, kind, detail FROM events ORDER BY ts DESC LIMIT 5").fetchall()
    print(f"database: {path}")
    print(f"samples: {n}")
    print(f"last sample: {_fmt(last)}")
    print(f"samples in last 10 min: {recent}")
    print("last events:")
    for ts, kind, detail in events:
        print(f"  {_fmt(ts)}  {kind}{'  ' + detail if detail else ''}")
    return 0


def cmd_check() -> int:
    from logger.macos.permissions import report, request_missing

    r = report(load_config())
    for name, ok in r.items():
        print(f"{name:45s} {'granted' if ok else ('not running' if ok is None else 'MISSING')}")
    if not r["accessibility"]:
        request_missing()
    return 0 if all(v is not False for v in r.values()) else 1


def main(argv: list[str] | None = None, key_store: KeyStore | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m logger")
    parser.add_argument("command", choices=["run", "check", "status"])
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    store = key_store or KeyringStore()
    if args.command == "run":
        return cmd_run(store)
    if args.command == "status":
        return cmd_status(store)
    return cmd_check()


if __name__ == "__main__":
    sys.exit(main())
