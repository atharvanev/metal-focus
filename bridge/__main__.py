"""CLI: uv run python -m bridge [serve|test-checkin|test-nudge]."""

from __future__ import annotations

import argparse
import sys

from bridge.store import Store


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m bridge")
    ap.add_argument("command", nargs="?", default="serve", choices=["serve", "test-checkin", "test-nudge"])
    args = ap.parse_args(argv)
    if args.command == "serve":
        from bridge.server import Server

        return Server(sys.stdin, sys.stdout, Store.open).serve()
    try:
        store = Store.open()
    except Exception as e:  # no DB / key missing / wrong key
        print(f"cannot open the database: {e}", file=sys.stderr)
        return 1
    oid = store.insert_test_prompt("checkin" if args.command == "test-checkin" else "nudge")
    print(oid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
