"""Live runner: feed each new 2-min window through the HSMM and persist the readout.

    uv run python -m hsmm.runner [run] [--params PATH] [--interval 30] [--no-nudge]
    uv run python -m hsmm.runner install | uninstall     # LaunchAgents (runner + nightly)

Each tick loads today's completed windows (data.windows.load_day), steps the
LiveHSMM over the ones not seen yet, and writes one row per window to the
`readouts` table; nudge/ consumes the new rows via the on_rows hook (below), and
the menu bar app and dashboard read the table directly.
A new local day resets the model (each day is one sequence,
matching training). After a process restart, or when windowing rebuilds a day
(e.g. a categorizer rule change), the day is replayed from its first window and
its rows are rewritten, so the belief is identical to a continuous run.

The DB is opened with data.db.open_existing(), which never creates a DB or key.
A missing DB, missing key, or wrong key means "can't run yet": retry later.

Params: the nightly-trained file (hsmm.nightly.TRAINED_PARAMS) if it exists,
else hsmm/cold_start_params.json (hand-set prior). A changed params file is
picked up only while no window of the current day has been processed yet (day
rollover, or the 03:30 nightly finishing before the first window of the day).
Once a day has readouts its params are fixed: a rebuild replays the day under
the same params, so today's readouts stay past-only under the params that
were live. Each row records the params' content hash.

After each tick, `on_rows(conn, day, new_rows, windows)` runs (the nudge engine
by default); a hook error is logged and never stops the runner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, Callable, Sequence

import sqlcipher3

from hsmm.live import LiveHSMM
from hsmm.params import HSMMParams

COLD_START_PARAMS = Path(__file__).with_name("cold_start_params.json")

# The real DB is a sqlcipher3 connection, whose exceptions do NOT inherit from
# sqlite3.Error; catch both (plain sqlite3 is used in tests).
DB_ERRORS: tuple[type[BaseException], ...] = (sqlite3.Error, sqlcipher3.Error)
MODEL_NAME = "hsmm"

SCHEMA = """
CREATE TABLE IF NOT EXISTS readouts (
    window_start REAL NOT NULL,      -- epoch s (UTC), same as windows.window_start
    model        TEXT NOT NULL,      -- 'hsmm' (later: 'gru', 'hybrid')
    day          TEXT NOT NULL,      -- local date YYYY-MM-DD
    written_at   REAL NOT NULL,      -- epoch s
    params       TEXT NOT NULL,      -- params version: "sha256:<12 hex>"
    readout      TEXT NOT NULL,      -- JSON of common.model_api.Readout (asdict)
    PRIMARY KEY (window_start, model)
);
CREATE INDEX IF NOT EXISTS readouts_day ON readouts(day);
"""

LoadDay = Callable[[Any, str], Sequence[Any]]


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def load_params(path: Path | None) -> tuple[HSMMParams, str]:
    """(params, version string). Falls back to the cold-start prior."""
    p = Path(path) if path is not None and Path(path).exists() else COLD_START_PARAMS
    version = "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()[:12]
    return HSMMParams.load(p), version


OnRows = Callable[[Any, str, list, list], Any]


class LiveRunner:
    def __init__(
        self,
        conn,
        load_day: LoadDay,
        params_path: Path | None = None,
        cfg: dict | None = None,
        on_rows: OnRows | None = None,
    ):
        self.conn = conn
        self.load_day = load_day
        self.params_path = params_path
        self.on_rows = on_rows
        params, self.params_used = load_params(params_path)
        self.model = LiveHSMM(params, cfg)
        self.day: str | None = None
        self.seen: list[tuple] = []  # fingerprints of windows of self.day already stepped
        ensure_schema(conn)

    def _start_day(self, day: str, reload: bool = True) -> None:
        if reload:
            params, version = load_params(self.params_path)
            if version != self.params_used:
                self.model.set_params(params)
                self.params_used = version
        self.model.reset()
        self.day, self.seen = day, []

    def tick(self, day: str | None = None) -> list[dict]:
        """Process new windows of `day` (default: today, local). Returns new rows written."""
        day = day or date.today().isoformat()
        if day != self.day or not self.seen:
            self._start_day(day)  # nothing processed today yet: newest params are fair game
        windows = list(self.load_day(self.conn, day))
        prints = [_fingerprint(w) for w in windows]
        if prints[: len(self.seen)] != self.seen:  # windowing rebuilt the day: replay, same params
            self._start_day(day, reload=False)
        rows = []
        for w in windows[len(self.seen) :]:
            r = self.model.step(w)
            rows.append(
                {
                    "window_start": float(w.window_start),
                    "model": MODEL_NAME,
                    "day": day,
                    "written_at": time.time(),
                    "params": str(self.params_used),
                    "readout": json.dumps(asdict(r)),
                }
            )
        self.seen = prints
        if rows:
            self.conn.executemany(
                "INSERT OR REPLACE INTO readouts (window_start, model, day, written_at, params, readout) "
                "VALUES (:window_start, :model, :day, :written_at, :params, :readout)",
                rows,
            )
            self.conn.commit()
            if self.on_rows is not None:
                try:
                    self.on_rows(self.conn, day, rows, windows)
                except DB_ERRORS:
                    raise  # connection problem: let main() reconnect
                except Exception as e:  # a hook bug must never stop the model
                    print(f"hsmm.runner: on_rows hook failed: {type(e).__name__}: {e}", flush=True)
        return rows


def _fingerprint(w: Any) -> tuple:
    """Everything the HSMM reads from a window; a change means the day was rebuilt."""
    return (
        float(w.window_start),
        getattr(w, "category", None),
        getattr(w, "switch_count", None),
        bool(getattr(w, "is_away", False)),
    )


class _CantRun(Exception):
    """DB missing / key missing / wrong key: wait and retry."""


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", nargs="?", default="run", choices=["run", "install", "uninstall"])
    ap.add_argument("--params", type=Path, default=None, help="params JSON (default: nightly-trained, else cold start)")
    ap.add_argument("--interval", type=float, default=30.0, help="seconds between ticks")
    ap.add_argument("--no-nudge", action="store_true", help="don't run the nudge engine hook")
    args = ap.parse_args(argv)
    from common.supervised import exit_with_parent

    exit_with_parent()

    if args.command in ("install", "uninstall"):
        from hsmm import launchd

        launchd.install() if args.command == "install" else launchd.uninstall()
        return

    from hsmm.nightly import trained_params_path

    params_path = args.params or trained_params_path()
    on_rows = None
    if not args.no_nudge:
        from nudge.engine import on_tick as on_rows

    runner, conn = None, None
    while True:
        try:
            if runner is None:
                # Imported inside the loop: under launchd KeepAlive a missing module must be
                # "can't run yet, retry", never a crash loop.
                import data.db as db  # type: ignore[import-not-found]
                from data.windows import load_day  # type: ignore[import-not-found]

                cant_run = tuple(
                    e
                    for e in (FileNotFoundError, getattr(db, "KeyMissingError", None), getattr(db, "WrongKeyError", None))
                    if e
                )
                try:
                    conn = db.open_existing()  # never creates a DB or key
                except cant_run as e:
                    raise _CantRun(e) from e
                runner = LiveRunner(conn, load_day, params_path, on_rows=on_rows)
            runner.tick()
        except (ImportError, _CantRun) as e:
            print(f"hsmm.runner: can't run yet ({type(e).__name__}: {e}); retrying", flush=True)
            runner = None
        except DB_ERRORS as e:  # connection went bad (locked, I/O): reopen next tick
            print(f"hsmm.runner: database error ({e}); reconnecting", flush=True)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            runner, conn = None, None
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
