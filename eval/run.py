"""Held-out-week evaluation: HSMM vs HMM baseline (vs rules baseline when available).

    uv run python -m eval.run --eval-start 2026-12-01 --eval-end 2026-12-07 [--train-days 28]

Leakage rules (CLAUDE.md):
- Models are fit here from the cold-start prior on the `train-days` days
  *before* the held-out week. The nightly params file is never loaded, because
  it has already trained on the held-out week.
- Readouts on the held-out week are past-only (models stepped live, reset per day).

Ground truth:
- PRIMARY: random-trigger check-ins (labels.checkins.load_eval), unbiased but
  sparse (~1/day). Reported with their counts.
- SECONDARY: merged labels (labels.merge.load_training_labels) on held-out days.
  Mostly auto-labels, so they partly reward copying the labeler's rules;
  episode metrics (lead time, catches) can only come from here for now.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict
from datetime import date, timedelta
from typing import Any, Callable, Sequence

from eval.metrics import checkin_truth, grade_episodes, grade_nudges
from hsmm.config import load_config
from nudge.rule import Point, replay
from nudge.rule import load_config as load_nudge_config


def readouts_for_days(model_factory: Callable[[], Any], days: Sequence[Sequence[Any]]) -> list[list[Any]]:
    out = []
    for wins in days:
        m = model_factory()  # fresh per day (each day is one sequence)
        out.append([m.step(w) for w in wins])
    return out


def fires_for(readouts: Sequence[Sequence[Any]], windows: Sequence[Sequence[Any]], ncfg: dict) -> list[list[int]]:
    return [
        replay([Point(float(w.window_start), r.risk, bool(r.away)) for w, r in zip(wins, rs)], ncfg)
        for wins, rs in zip(windows, readouts)
    ]


def evaluate(
    models: dict[str, dict],
    eval_windows: Sequence[Sequence[Any]],
    truth_primary: Sequence[Sequence[int | None]] | None,
    truth_secondary: Sequence[Sequence[int | None]] | None,
    ncfg: dict | None = None,
) -> dict:
    """models: name -> {"live": factory() -> LiveModel, "loglik": fn() -> (total, n_windows) | None}."""
    ncfg = ncfg or load_nudge_config()
    hcfg = load_config()
    report: dict = {"n_days": len(eval_windows), "models": {}}
    if truth_primary is not None:
        report["n_checkin_windows"] = sum(x is not None for d in truth_primary for x in d)
    for name, spec in models.items():
        rs = readouts_for_days(spec["live"], eval_windows)
        fires = fires_for(rs, eval_windows, ncfg)
        r: dict = {"nudges_per_day": sum(map(len, fires)) / max(len(fires), 1)}
        if spec.get("loglik"):
            total, n = spec["loglik"]()
            r["loglik_per_window"] = total / n if n else None
        if truth_primary is not None:
            r["primary_checkins"] = asdict(grade_nudges(fires, truth_primary))
        if truth_secondary is not None:
            r["secondary_merged_labels"] = {
                "nudges": asdict(grade_nudges(fires, truth_secondary)),
                "episodes": asdict(grade_episodes(fires, truth_secondary, hcfg["window_minutes"])),
            }
        report["models"][name] = r
    return report


def _labels_to_truth(labels_by_day, states) -> list[list[int | None]]:
    return [[states.index(s) if s in states else None for s, _src in day] for day in labels_by_day]


def build_models(train_obs, eval_obs, hcfg: dict) -> dict[str, dict]:
    """Fit HSMM and HMM from the cold-start prior on training days only."""
    from baselines.hmm import HMMParams, LiveHMM
    from baselines.hmm import fit_em as hmm_fit
    from baselines.hmm import forward as hmm_forward
    from hsmm.forward import forward as hsmm_forward
    from hsmm.io import days_to_batch
    from hsmm.live import LiveHSMM
    from hsmm.params import HSMMParams
    from hsmm.runner import COLD_START_PARAMS
    from hsmm.train import fit_em

    cold = HSMMParams.load(COLD_START_PARAMS)
    train_b = days_to_batch(train_obs, hcfg)
    eval_b = days_to_batch(eval_obs, hcfg)
    hsmm_p, _ = fit_em(cold, train_b, n_iter=30, prior=hcfg["prior"])
    hmm_p, _ = hmm_fit(HMMParams.from_hsmm(cold), train_b, n_iter=30, prior=float(hcfg["prior"]["B"]))
    n_active = float(eval_b.active.sum().item())

    def hsmm_ll():
        fr = hsmm_forward(hsmm_p.derived(), eval_b, use_labels=False)
        return float(fr.loglik.sum().item()), n_active

    def hmm_ll():
        _, logc, _ = hmm_forward(hmm_p, eval_b, use_labels=False)
        return float(logc.sum().item()), n_active

    models = {
        "hsmm": {"live": lambda: LiveHSMM(hsmm_p, hcfg), "loglik": hsmm_ll, "params": hsmm_p},
        "hmm": {"live": lambda: LiveHMM(hmm_p, hcfg), "loglik": hmm_ll, "params": hmm_p},
    }
    try:  # rules baseline (metal-focus-d1): the auto-labeler's rules run live, LiveModel
        from baselines.rules import RulesBaseline

        models["rules"] = {"live": RulesBaseline}
    except ImportError:
        pass
    return models


def _days(start: date, end: date) -> list[str]:
    return [(start + timedelta(days=k)).isoformat() for k in range((end - start).days + 1)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--eval-start", required=True, type=date.fromisoformat)
    ap.add_argument("--eval-end", required=True, type=date.fromisoformat)
    ap.add_argument("--train-days", type=int, default=28)
    args = ap.parse_args(argv)

    import data.db as db  # type: ignore[import-not-found]
    from data.windows import load_day  # type: ignore[import-not-found]
    from hsmm.io import day_to_obs
    from labels.checkins import load_eval  # type: ignore[import-not-found]
    from labels.merge import load_training_labels  # type: ignore[import-not-found]

    hcfg = load_config()
    conn = db.open_existing()
    train_days = _days(args.eval_start - timedelta(days=args.train_days), args.eval_start - timedelta(days=1))
    eval_days = _days(args.eval_start, args.eval_end)

    train_obs = [day_to_obs(w, load_training_labels(conn, d), hcfg) for d in train_days if (w := load_day(conn, d))]
    eval_windows, eval_labels = [], []
    for d in eval_days:
        w = load_day(conn, d)
        if w:
            eval_windows.append(w)
            eval_labels.append(load_training_labels(conn, d))
    if not train_obs or not eval_windows:
        print(json.dumps({"status": "skipped", "train_days_with_data": len(train_obs), "eval_days_with_data": len(eval_windows)}))
        return 1
    eval_obs = [day_to_obs(w, None, hcfg) for w in eval_windows]

    checkins = load_eval(conn, args.eval_start.isoformat(), args.eval_end.isoformat())
    primary = checkin_truth([[float(x.window_start) for x in w] for w in eval_windows], checkins, hcfg["states"])
    secondary = _labels_to_truth(eval_labels, hcfg["states"])
    models = build_models(train_obs, eval_obs, hcfg)
    report = evaluate({k: v for k, v in models.items()}, eval_windows, primary, secondary)
    report.update(train_days=len(train_obs), n_checkins=len(checkins))
    print(json.dumps(report, indent=2, default=lambda x: None if isinstance(x, float) and math.isnan(x) else str(x)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
