from dataclasses import replace

import pytest

from data.windows import Window
from labels.checkins import CheckinRequest, Context, decide, load_checkin_config, random_time

DAY_START = 1_794_528_000.0  # 2026-11-13 00:00 UTC (tz 0 in these tests)
NOON = DAY_START + 12 * 3600
CFG = load_checkin_config()
SITE = {"W": None, "D": "reddit.com", "Y": "youtube.com", "A": None}
CAT = {"W": "Coding", "D": "Social", "Y": "Video", "A": "Other"}


def wins(pattern, end=NOON, typing=0.5):
    n = len(pattern)
    return [Window(window_start=end - 120 * (n - i), tz_offset_min=0, day="2026-11-13", category=CAT[ch],
                   switch_count=0, idle_seconds=0.0, covered_seconds=120.0, is_away=ch == "A",
                   typing_frac=typing if ch == "W" else 0.0, click_frac=0.0, scroll_frac=0.0,
                   dominant=SITE[ch], low_confidence=ch == "Y") for i, ch in enumerate(pattern)]


def ro(p):
    top = max(range(4), key=lambda i: p[i])
    return {"p_state": list(p), "top_state": top, "top_prob": p[top], "away": False}


CONFIDENT = ro((0.9, 0.05, 0.03, 0.02))


def ctx(pattern="WWWW", now=NOON, readouts=None, history=(), last_nudge_at=None, days_logging=10,
        current=None, random_at=None, typing=0.5):
    ws = wins(pattern, end=now, typing=typing)
    return Context(now=now, tz_offset_min=0, windows=ws, readouts=readouts or [CONFIDENT] * len(ws),
                   current=current or {"bundle_id": "com.microsoft.VSCode", "domain": None, "fullscreen": 0},
                   history=list(history), last_nudge_at=last_nudge_at, days_logging=days_logging,
                   random_at=random_at)


def asked(at, trigger="gray_zone", response="Working", ws=None, we=None):
    return {"asked_at": at, "trigger": trigger, "response": response, "window_start": ws, "window_end": we}


def test_quiet_working_day_asks_nothing():
    assert decide(ctx(), CFG) is None


def test_gray_zone_session_asks_about_last_ten_minutes():
    req = decide(ctx("WW" + "D" * 6), CFG)
    assert req == CheckinRequest("gray_zone", NOON - 600, NOON, "train", "Working", 0.9)


@pytest.mark.parametrize("n", [4, 10])
def test_gray_zone_bounds(n):
    assert decide(ctx("WW" + "D" * n), CFG) is None


def test_uncertain_beats_gray_zone():
    unsure = ro((0.5, 0.2, 0.2, 0.1))
    req = decide(ctx("WW" + "D" * 6, readouts=[CONFIDENT] * 6 + [unsure, unsure]), CFG)
    assert req.trigger == "uncertain" and req.model_top_prob == 0.5


def test_uncertain_needs_two_windows():
    unsure = ro((0.5, 0.2, 0.2, 0.1))
    assert decide(ctx("WWWW", readouts=[CONFIDENT] * 3 + [unsure]), CFG) is None


def test_uncertain_short_break_vs_drifting_close():
    close = ro((0.1, 0.42, 0.44, 0.04))
    assert decide(ctx("WWDD", readouts=[CONFIDENT, CONFIDENT, CONFIDENT, close]), CFG).trigger == "uncertain"


def test_ambiguous_app_first_ten_minutes_of_day():
    req = decide(ctx("YYYYY", current={"bundle_id": "com.google.Chrome", "domain": "youtube.com", "fullscreen": 0}), CFG)
    assert req.trigger == "ambiguous_app"


def test_ambiguous_app_only_once_per_site_per_day():
    h = [asked(NOON - 4000, "ambiguous_app", ws=NOON - 600, we=NOON)]
    c = ctx("YYYYY", history=h, current={"bundle_id": "com.google.Chrome", "domain": "youtube.com", "fullscreen": 0})
    assert decide(c, CFG) is None


def test_random_prompt_is_eval_split_and_once_a_day():
    req = decide(ctx(random_at=NOON - 1), CFG)
    assert (req.trigger, req.split) == ("random", "eval")
    assert decide(ctx(random_at=NOON - 1, history=[asked(NOON - 4000, "random")]), CFG) is None


def test_random_not_due_yet():
    assert decide(ctx(random_at=NOON + 60), CFG) is None


@pytest.mark.parametrize("kw", [
    dict(now=DAY_START + 3 * 3600),                                    # quiet hours
    dict(last_nudge_at=NOON - 300),                                    # within 10 min of a nudge
    dict(current={"bundle_id": "com.apple.Safari", "domain": None, "fullscreen": 1}),
    dict(current={"bundle_id": "us.zoom.xos", "domain": None, "fullscreen": 0}),
    dict(current={"bundle_id": "com.google.Chrome", "domain": "meet.google.com", "fullscreen": 0}),
])
def test_suppression_rules(kw):
    now = kw.pop("now", NOON)
    assert decide(ctx("WW" + "D" * 6, now=now, random_at=now - 1, **kw), CFG) is None


def test_no_interrupting_a_focused_work_stretch():
    assert decide(ctx("W" * 13, random_at=NOON - 1), CFG) is None
    assert decide(ctx("W" * 13, random_at=NOON - 1, typing=0.0), CFG).trigger == "random"  # not typing steadily


def test_min_gap_between_checkins():
    assert decide(ctx("WW" + "D" * 6, history=[asked(NOON - 1800)]), CFG) is None
    assert decide(ctx("WW" + "D" * 6, history=[asked(NOON - 3700)]), CFG).trigger == "gray_zone"


def test_daily_budget_by_phase_with_random_reserved():
    two_today = [asked(NOON - 4 * 3600), asked(NOON - 2 * 3600)]
    assert decide(ctx("WW" + "D" * 6, history=two_today, days_logging=3), CFG) is not None   # week 1: 6/day
    assert decide(ctx("WW" + "D" * 6, history=two_today, days_logging=30), CFG) is None      # later: 2/day
    one = [asked(NOON - 2 * 3600)]
    assert decide(ctx("WW" + "D" * 6, history=one, days_logging=30), CFG) is None            # last slot reserved
    assert decide(ctx(history=one, days_logging=30, random_at=NOON - 1), CFG).trigger == "random"


def test_skip_streak_backs_off_to_one_a_day():
    skips = [asked(NOON - d * 86400 - 3600, response="skip") for d in (1, 1.1, 1.2)]
    assert decide(ctx("WW" + "D" * 6, history=skips + [asked(NOON - 7200, response=None)], days_logging=3), CFG) is None
    assert decide(ctx("WW" + "D" * 6, history=skips, days_logging=3), CFG) is not None
    old = [asked(NOON - 5 * 86400 - d, response="skip") for d in (1, 2, 3)]
    assert decide(ctx("WW" + "D" * 6, history=old + [asked(NOON - 7200, response=None)], days_logging=10), CFG) is not None


def test_dismissed_does_not_count_as_skip():
    dis = [asked(NOON - 86400 - d, response="dismissed") for d in (1, 2, 3)]
    assert decide(ctx("WW" + "D" * 6, history=dis + [asked(NOON - 7200, response=None)], days_logging=3), CFG) is not None


def test_random_time_is_stable_and_inside_hours():
    t1, t2 = random_time("2026-11-13", 0, CFG), random_time("2026-11-13", 0, CFG)
    assert t1 == t2 and DAY_START + 9 * 3600 <= t1 < DAY_START + 22 * 3600
    assert random_time("2026-11-14", 0, CFG) != t1


def test_away_readouts_are_ignored_for_uncertainty():
    unsure = {**ro((0.5, 0.2, 0.2, 0.1)), "away": True}
    assert decide(ctx("WWWW", readouts=[CONFIDENT, CONFIDENT, unsure, unsure]), CFG) is None


def test_gray_zone_needs_more_than_ten_minutes():
    assert decide(ctx("WW" + "D" * 5), CFG) is None
