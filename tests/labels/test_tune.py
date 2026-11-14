from dataclasses import replace

import pytest

from labels.config import load_labels_config
from labels.tune import agreement, apply_setting, grid, suggest_from_days
from tests.labels.test_autolabel import wins

CFG = load_labels_config()


def day_with_sessions(n_sessions, length, user_says):
    """Work, then a `length`-window distraction session, repeated; one check-in per session."""
    pattern = ("WWW" + "D" * length) * n_sessions + "WWW"
    ws = wins(pattern)
    checkins, i = [], 0
    for _ in range(n_sessions):
        i += 3
        # check-in covers the last part of the session ("the last 10 min")
        start = ws[i + length - min(5, length)].window_start
        checkins.append({"response": user_says, "window_start": start, "window_end": ws[i + length - 1].window_start + 120,
                         "split": "train"})
        i += length
    return ws, checkins


def test_agreement_counts_only_comparable_checkins():
    ws, ck = day_with_sessions(2, 3, "Short break")
    a = agreement([(ws, ck)], CFG)
    assert (a.agree, a.comparable, a.total) == (2, 2, 2)
    ws2, ck2 = day_with_sessions(1, 7, "Rabbit hole")  # gray zone at defaults -> unlabeled
    a2 = agreement([(ws2, ck2)], CFG)
    assert (a2.comparable, a2.total) == (0, 1)


def test_eval_and_non_state_checkins_ignored():
    ws, ck = day_with_sessions(1, 3, "Short break")
    ck = [{**ck[0], "split": "eval"}, {**ck[0], "response": "skip"}]
    assert agreement([(ws, ck)], CFG).total == 0


def test_grid_matches_spec_ranges():
    g = list(grid())
    assert {s["RABBIT_MIN"] for s in g} == set(range(6, 15))
    assert {s["BREAK_MAX"] for s in g} == set(range(3, 8))
    assert {s["HOPS_MIN"] for s in g} == {2, 3, 4}


def test_suggests_shorter_rabbit_min_when_user_calls_16_min_sessions_rabbit_holes():
    days = [day_with_sessions(4, 8, "Rabbit hole") for _ in range(5)] + [day_with_sessions(4, 3, "Short break") for _ in range(4)]
    s = suggest_from_days(days, CFG, min_checkins=30)
    assert s is not None and s.best["RABBIT_MIN"] <= 8
    assert s.best_agreement.rate > s.current_agreement.rate
    assert "16 min" in s.message or "min" in s.message


def test_needs_enough_checkins():
    days = [day_with_sessions(2, 8, "Rabbit hole")]
    s = suggest_from_days(days, CFG, min_checkins=30)
    assert s is None


def test_no_suggestion_when_current_is_best():
    days = [day_with_sessions(4, 3, "Short break") for _ in range(9)]
    s = suggest_from_days(days, CFG, min_checkins=30)
    assert s is not None and s.best is None and "keep" in s.message.lower()


def test_apply_setting_rewrites_yaml_and_logs(tmp_path):
    from labels.config import DEFAULT_PATH, load_labels_config

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(DEFAULT_PATH.read_text())
    log = tmp_path / "tuning_log.csv"
    apply_setting({"RABBIT_MIN": 9, "BREAK_MAX": 5, "HOPS_MIN": 3}, 0.83, 0.86, cfg_path, log, today="2026-11-20")
    new = load_labels_config(cfg_path)
    assert (new.RABBIT_MIN, new.BREAK_MAX, new.HOPS_MIN) == (9, 5, 3)
    assert "checkins:" in cfg_path.read_text()  # rest of the file (and comments) kept
    assert "# Auto-labeler thresholds" in cfg_path.read_text()
    lines = log.read_text().strip().splitlines()
    assert lines[0].startswith("date,") and "2026-11-20" in lines[1] and "RABBIT_MIN=10->9" in lines[1] and "0.830" in lines[1]
