"""Nightly HSMM update on the last 2-4 weeks of completed days.

    uv run python -m hsmm.nightly [--days-back 28] [--min-days 3] [--dry-run]

- Days: completed local days before today (today is never trained on).
- Inputs: data.windows.load_day + labels.merge.load_training_labels (eval-split
  check-ins already excluded there) via hsmm.io.load_training_batch.
- Update: hsmm.train.nightly_update, starting from the current trained params
  (or the cold-start prior).
- Output: written atomically to trained_params_path(); a dated copy goes to
  params_history/. Non-finite params are never saved.
The live runner picks the new file up at its next day rollover.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import mlx.core as mx

import data.db as db
from hsmm.params import HSMMParams


def trained_params_path() -> Path:
    from common.paths import data_dir

    return data_dir() / "hsmm_params.json"


def history_dir() -> Path:
    from common.paths import data_dir

    d = data_dir() / "hsmm_params_history"
    d.mkdir(exist_ok=True)
    return d


def training_days(today: date, days_back: int) -> list[str]:
    return [(today - timedelta(days=k)).isoformat() for k in range(days_back, 0, -1)]


def is_finite(p: HSMMParams) -> bool:
    return all(mx.all(mx.isfinite(getattr(p, k))).item() for k in ("pi", "A", "B", "C", "mu", "r"))


def save_atomic(params: HSMMParams, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(params.to_dict(), f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def run_nightly(conn, today: date, days_back: int = 28, min_days: int = 3, out: Path | None = None, dry_run: bool = False) -> dict:
    from hsmm.config import load_config
    from hsmm.io import day_to_obs, days_to_batch
    from hsmm.runner import load_params
    from hsmm.train import nightly_update

    from data.windows import load_day  # type: ignore[import-not-found]
    from labels.merge import load_training_labels  # type: ignore[import-not-found]

    cfg = load_config()
    out = out or trained_params_path()
    obs, used = [], []
    for d in training_days(today, days_back):
        wins = load_day(conn, d)
        if not wins:
            continue
        obs.append(day_to_obs(wins, load_training_labels(conn, d), cfg))
        used.append(d)
    if len(used) < min_days:
        return {"status": "skipped", "reason": f"only {len(used)} day(s) with data (< {min_days})", "days": used}

    params, version = load_params(out)
    new, counts = nightly_update(params, days_to_batch(obs, cfg), cfg)
    if not is_finite(new):
        return {"status": "error", "reason": "non-finite params; not saved", "days": used}
    summary = {
        "status": "dry-run" if dry_run else "saved",
        "days": used,
        "from_params": version,
        "loglik": counts.loglik,
        "occupancy": [round(x, 1) for x in counts.occ.tolist()],
        "mu_minutes": [round(x * cfg["window_minutes"], 1) for x in new.mu.tolist()],
    }
    if not dry_run:
        save_atomic(new, out)
        save_atomic(new, history_dir() / f"{today.isoformat()}.json")
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days-back", type=int, default=28)
    ap.add_argument("--min-days", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    try:
        conn = db.open_existing()
    except Exception as e:  # FileNotFoundError / KeyMissingError / WrongKeyError
        print(f"hsmm.nightly: can't open DB ({type(e).__name__}: {e})", file=sys.stderr)
        return 1
    try:
        summary = run_nightly(conn, date.today(), args.days_back, args.min_days, dry_run=args.dry_run)
    finally:
        conn.close()
    print(json.dumps(summary))
    return 0 if summary["status"] in ("saved", "dry-run", "skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
