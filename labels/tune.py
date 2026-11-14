"""Weekly threshold tuning (suggestion only): replay the auto-labeler with a grid of thresholds and
measure agreement with train-split check-ins. Nothing changes until the user runs `apply`.

Agreement: each train-split check-in (state responses only) is compared with the majority auto-label
over the windows it covers. A check-in whose windows the setting leaves unlabeled counts as a miss,
so a setting can't score well by labeling less. Needs ~30+ check-ins before suggesting anything.

  uv run python -m labels.tune suggest [--weeks 4]
  uv run python -m labels.tune apply RABBIT_MIN=9 [BREAK_MAX=5 HOPS_MIN=3]
Every applied change is logged (date, old->new, agreement before/after) in labels/tuning_log.csv.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import re
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from data.windows import WINDOW_S, Window
from labels.autolabel import auto_label
from labels.config import DEFAULT_PATH, LabelsConfig, load_labels_config

STATES = ("Working", "Short break", "Drifting", "Rabbit hole", "Away")
KEYS = ("RABBIT_MIN", "BREAK_MAX", "HOPS_MIN")
LOG_PATH = Path(__file__).with_name("tuning_log.csv")
FLAG_BELOW = 0.80
Day = tuple[Sequence[Window], Sequence[Mapping[str, Any]]]


@dataclass(frozen=True)
class Agreement:
    agree: int
    comparable: int  # check-ins whose windows got any auto-label
    total: int

    @property
    def rate(self) -> float:
        return self.agree / self.total if self.total else 0.0


def agreement(days: Iterable[Day], cfg: LabelsConfig) -> Agreement:
    agree = comparable = total = 0
    for windows, checkins in days:
        labels = auto_label(windows, cfg)
        for c in checkins:
            if c.get("split") != "train" or c.get("response") not in STATES:
                continue
            total += 1
            covered = [labels[i] for i, w in enumerate(windows)
                       if c["window_start"] <= w.window_start < c["window_end"] and labels[i] is not None]
            if covered:
                comparable += 1
                agree += Counter(covered).most_common(1)[0][0] == c["response"]
    return Agreement(agree, comparable, total)


def grid() -> Iterable[dict[str, int]]:
    for rm, bm, hm in itertools.product(range(6, 15), range(3, 8), range(2, 5)):
        if bm < rm:
            yield {"RABBIT_MIN": rm, "BREAK_MAX": bm, "HOPS_MIN": hm}


@dataclass(frozen=True)
class Suggestion:
    best: dict[str, int] | None  # None: keep the current thresholds
    best_agreement: Agreement
    current_agreement: Agreement
    message: str


def _describe(setting: Mapping[str, int], cfg: LabelsConfig) -> str:
    parts = []
    for k in KEYS:
        if setting[k] != getattr(cfg, k):
            unit = f"{setting[k]} windows ({setting[k] * WINDOW_S // 60} min)" if k != "HOPS_MIN" else f"{setting[k]} sites"
            parts.append(f"{k} {unit}")
    return ", ".join(parts)


def suggest_from_days(days: Sequence[Day], cfg: LabelsConfig, min_checkins: int = 30) -> Suggestion | None:
    current = agreement(days, cfg)
    if current.total < min_checkins:
        return None
    cur = {k: getattr(cfg, k) for k in KEYS}

    def distance(s: Mapping[str, int]) -> int:
        return sum(abs(s[k] - cur[k]) for k in KEYS)

    scored = [(agreement(days, replace(cfg, **s)), s) for s in grid()]
    best_a, best = max(scored, key=lambda t: (t[0].rate, -distance(t[1])))
    flag = f" Agreement is below {FLAG_BELOW:.0%}: the thresholds need retuning." if current.rate < FLAG_BELOW else ""
    if best_a.rate <= current.rate or best == cur:
        return Suggestion(None, current, current,
                          f"Keep the current thresholds: they fit your check-ins at {current.rate:.0%} "
                          f"({current.total} check-ins).{flag}")
    msg = (f"{_describe(best, cfg)} fits your check-ins at {best_a.rate:.0%} vs {current.rate:.0%} now "
           f"({current.total} check-ins).{flag} Apply with: uv run python -m labels.tune apply "
           + " ".join(f"{k}={best[k]}" for k in KEYS))
    return Suggestion(best, best_a, current, msg)


def apply_setting(setting: Mapping[str, int], before: float, after: float, cfg_path: Path = DEFAULT_PATH,
                  log_path: Path = LOG_PATH, today: str | None = None) -> None:
    """Write the approved thresholds into labels/config.yaml (keeping comments) and log the change."""
    old = load_labels_config(cfg_path)
    text = cfg_path.read_text()
    changes = []
    for k in KEYS:
        v = int(setting.get(k, getattr(old, k)))
        text, n = re.subn(rf"^({k}:\s*)\d+", rf"\g<1>{v}", text, count=1, flags=re.M)
        if n != 1:
            raise ValueError(f"{k} not found in {cfg_path}")
        if v != getattr(old, k):
            changes.append(f"{k}={getattr(old, k)}->{v}")
    cfg_path.write_text(text)
    load_labels_config(cfg_path)  # validate
    new_file = not log_path.exists()
    with log_path.open("a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["date", "changes", "agreement_before", "agreement_after"])
        w.writerow([today or datetime.now().strftime("%Y-%m-%d"), ";".join(changes) or "none",
                    f"{before:.3f}", f"{after:.3f}"])


def load_days(conn, weeks: int, today: str | None = None) -> list[Day]:
    from data.windows import load_day
    from labels.checkins import _rows

    end = datetime.strptime(today or datetime.now().strftime("%Y-%m-%d"), "%Y-%m-%d")
    days: list[Day] = []
    for i in range(weeks * 7, 0, -1):  # completed days only
        d = (end - timedelta(days=i)).strftime("%Y-%m-%d")
        ws = load_day(conn, d)
        if not ws:
            continue
        ck = _rows(conn, "SELECT response, window_start, window_end, split FROM checkins "
                         "WHERE split = 'train' AND window_end > ? AND window_start < ?",
                   (ws[0].window_start, ws[-1].window_start + WINDOW_S))
        days.append((ws, ck))
    return days


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m labels.tune")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("suggest")
    s.add_argument("--weeks", type=int, default=4)
    a = sub.add_parser("apply")
    a.add_argument("settings", nargs="+", help="KEY=VALUE, e.g. RABBIT_MIN=9")
    a.add_argument("--weeks", type=int, default=4)
    args = p.parse_args(argv)
    from data.db import open_existing

    conn = open_existing()
    cfg = load_labels_config()
    days = load_days(conn, args.weeks)
    if args.cmd == "suggest":
        sug = suggest_from_days(days, cfg)
        print(sug.message if sug else f"Not enough check-ins yet ({agreement(days, cfg).total}; need 30).")
        return 0
    setting = {k: int(v) for k, v in (x.split("=", 1) for x in args.settings)}
    unknown = set(setting) - set(KEYS)
    if unknown:
        p.error(f"unknown keys: {sorted(unknown)}")
    before = agreement(days, cfg).rate
    after = agreement(days, replace(cfg, **setting)).rate
    apply_setting(setting, before, after)
    print(f"applied {setting}; agreement {before:.0%} -> {after:.0%} (logged in {LOG_PATH})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
