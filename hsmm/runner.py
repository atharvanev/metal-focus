"""Live runner: feed each new 2-min window through the HSMM and persist the readout.

    uv run python -m hsmm.runner [--params PATH] [--interval 30]

Each tick loads today's completed windows (data.windows.load_day), steps the
LiveHSMM over the ones not seen yet, and writes one row per window to the
`readouts` table. The menu bar bridge reads the latest row; nudge/ can read
the same rows. A new local day resets the model (each day is one sequence,
matching training). After a process restart the day is replayed from its first
window, so the belief is identical to a continuous run; rows already written
are left as they are.

Params: the nightly-trained file if it exists, else hsmm/cold_start_params.json
(hand-set prior). A changed params file is picked up at the next day rollover.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, Callable, Sequence

from hsmm.live import LiveHSMM
from hsmm.params import HSMMParams

COLD_START_PARAMS = Path(__file__).with_name("cold_start_params.json")
MODEL_NAME = "hsmm"

SCHEMA = """
CREATE TABLE IF NOT EXISTS readouts (
    window_start REAL NOT NULL,      -- epoch s (UTC), same as windows.window_start
    model        TEXT NOT NULL,      -- 'hsmm' (later: 'gru', 'hybrid')
    day          TEXT NOT NULL,      -- local date YYYY-MM-DD
    written_at   REAL NOT NULL,      -- epoch s
    params       TEXT NOT NULL,      -- params file used (path)
    readout      TEXT NOT NULL,      -- JSON of common.model_api.Readout (asdict)
    PRIMARY KEY (window_start, model)
);
CREATE INDEX IF NOT EXISTS readouts_day ON readouts(day);
"""

LoadDay = Callable[[Any, str], Sequence[Any]]


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def load_params(path: Path | None) -> tuple[HSMMParams, Path]:
    p = path if path is not None and Path(path).exists() else COLD_START_PARAMS
    return HSMMParams.load(p), Path(p)


class LiveRunner:
    def __init__(self, conn, load_day: LoadDay, params_path: Path | None = None, cfg: dict | None = None):
        self.conn = conn
        self.load_day = load_day
        self.params_path = params_path
        params, self.params_used = load_params(params_path)
        self.model = LiveHSMM(params, cfg)
        self.day: str | None = None
        self.seen = 0  # windows of self.day already stepped
        ensure_schema(conn)

    def _start_day(self, day: str) -> None:
        params, self.params_used = load_params(self.params_path)
        self.model.set_params(params)
        self.model.reset()
        self.day, self.seen = day, 0

    def tick(self, day: str | None = None) -> list[dict]:
        """Process new windows of `day` (default: today, local). Returns new rows written."""
        day = day or date.today().isoformat()
        if day != self.day:
            self._start_day(day)
        windows = self.load_day(self.conn, day)
        if len(windows) < self.seen:  # windowing rewrote the day: replay from scratch
            self._start_day(day)
        rows = []
        for w in windows[self.seen :]:
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
        self.seen = len(windows)
        if rows:
            self.conn.executemany(
                "INSERT OR IGNORE INTO readouts (window_start, model, day, written_at, params, readout) "
                "VALUES (:window_start, :model, :day, :written_at, :params, :readout)",
                rows,
            )
            self.conn.commit()
        return rows


def latest_readout(conn, model: str = MODEL_NAME) -> dict | None:
    """What the bridge reads: the most recent row, readout JSON decoded."""
    row = conn.execute(
        "SELECT window_start, day, written_at, readout FROM readouts WHERE model = ? "
        "ORDER BY window_start DESC LIMIT 1",
        (model,),
    ).fetchone()
    if row is None:
        return None
    return {"window_start": row[0], "day": row[1], "written_at": row[2], **json.loads(row[3])}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--params", type=Path, default=None, help="trained params JSON (default: cold start)")
    ap.add_argument("--interval", type=float, default=30.0, help="seconds between ticks")
    args = ap.parse_args(argv)

    from data.db import connect  # type: ignore[import-not-found]  # logger branch
    from data.windows import load_day  # type: ignore[import-not-found]

    conn = connect()
    runner = LiveRunner(conn, load_day, args.params)
    while True:
        runner.tick()
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
