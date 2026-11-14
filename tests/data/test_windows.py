from datetime import datetime, timedelta, timezone

import pytest

from data.windows import WINDOW_S, build_windows, load_day
from labels.categorizer import load_rules

TZ = -480  # PST, minutes
DAY = "2026-11-13"
# Local midnight 2026-11-13 PST as UTC epoch seconds
MIDNIGHT = datetime(2026, 11, 13, tzinfo=timezone(timedelta(minutes=TZ))).timestamp()
NOON = MIDNIGHT + 12 * 3600


@pytest.fixture(scope="module")
def rules():
    return load_rules()


def s(ts, bundle_id="com.microsoft.VSCode", domain=None, title="file.py", dt=4.0, tz=TZ, idle_s=0.5,
      switches=0, key=1, click=0, scroll=0, locked=0, excluded=0, private=None):
    return dict(ts=ts, tz_offset_min=tz, dt=dt, bundle_id=bundle_id, app_name=None, window_title=title,
                domain=domain, private=private, excluded=excluded, fullscreen=0, locked=locked,
                idle_s=idle_s, app_switches=switches, key_active=key, click_active=click, scroll_active=scroll)


def fill(start, n, **kw):
    """n samples 4 s apart, the first one 4 s after `start` (each covers the 4 s before it)."""
    return [s(start + 4 * (i + 1) - 0.001, **kw) for i in range(n)]


def build(samples, rules, now=None, day=DAY):
    return build_windows(samples, day=day, now=now if now is not None else NOON + 10 * 3600, rules=rules)


def test_dominant_category_by_time(rules):
    samples = fill(NOON, 20) + fill(NOON + 80, 10, bundle_id="com.google.Chrome", domain="reddit.com", title="r/x")
    [w] = build(samples, rules)
    assert (w.window_start, w.category, w.dominant, w.day) == (NOON, "Coding", "com.microsoft.VSCode", DAY)
    assert w.covered_seconds == pytest.approx(120, abs=0.01) and not w.is_away


def test_windows_align_to_local_two_minute_boundaries(rules):
    nepal = 345  # +05:45, not a whole number of 2-minute blocks from UTC
    base = datetime(2026, 11, 13, 12, 0, tzinfo=timezone(timedelta(minutes=nepal))).timestamp()
    [w] = build(fill(base, 30, tz=nepal), rules)
    assert (w.window_start + nepal * 60) % WINDOW_S == 0
    assert w.window_start == base and w.tz_offset_min == nepal


def test_only_completed_windows(rules):
    samples = fill(NOON, 30) + fill(NOON + 120, 10)
    assert [w.window_start for w in build(samples, rules, now=NOON + 150)] == [NOON]
    assert len(build(samples, rules, now=NOON + 240)) == 2


def test_gap_emits_away_windows(rules):
    samples = fill(NOON, 30) + fill(NOON + 5 * 120, 30)
    ws = build(samples, rules)
    assert [w.window_start for w in ws] == [NOON + 120 * i for i in range(6)]
    assert [w.is_away for w in ws] == [False, True, True, True, True, False]
    assert ws[2].covered_seconds == 0 and ws[2].category == "Other" and ws[2].dominant is None


def test_no_trailing_away_windows_after_last_sample(rules):
    assert len(build(fill(NOON, 30), rules, now=NOON + 3600)) == 1


def test_away_by_idle(rules):
    [w] = build(fill(NOON, 30, idle_s=200.0, key=0), rules)
    assert w.idle_seconds == pytest.approx(120, abs=0.01) and w.is_away


def test_partly_idle_is_not_away(rules):
    samples = fill(NOON, 15, idle_s=200.0) + fill(NOON + 60, 15)
    [w] = build(samples, rules)
    assert w.idle_seconds == pytest.approx(60 + 15 * 0.5, abs=0.01) and not w.is_away


def test_away_by_lock(rules):
    [w] = build(fill(NOON, 30, locked=1, bundle_id=None), rules)
    assert w.is_away


def test_away_by_low_coverage(rules):
    [w] = build(fill(NOON, 10), rules)  # 40 s of samples
    assert w.covered_seconds == pytest.approx(40, abs=0.01) and w.is_away


def test_switches_and_activity_fractions(rules):
    samples = fill(NOON, 10, switches=1, key=1) + fill(NOON + 40, 20, key=0, click=None, scroll=None)
    [w] = build(samples, rules)
    assert w.switch_count == 10
    assert w.typing_frac == pytest.approx(10 / 30)
    assert w.click_frac == 0.0  # only measured samples count
    assert w.scroll_frac == 0.0


def test_activity_none_when_never_measured(rules):
    [w] = build(fill(NOON, 30, key=None, click=None, scroll=None), rules)
    assert (w.typing_frac, w.click_frac, w.scroll_frac) == (None, None, None)


def test_excluded_dominates(rules):
    samples = fill(NOON, 20, bundle_id=None, title=None, excluded=1) + fill(NOON + 80, 10)
    [w] = build(samples, rules)
    assert w.category == "Excluded" and w.dominant is None and not w.low_confidence


def test_low_confidence_follows_dominant(rules):
    [w] = build(fill(NOON, 30, bundle_id="com.google.Chrome", domain="youtube.com", title="cats"), rules)
    assert (w.category, w.low_confidence, w.dominant) == ("Video", True, "youtube.com")


def test_day_split_at_local_midnight(rules):
    samples = fill(MIDNIGHT - 120, 30) + fill(MIDNIGHT, 30)
    assert [w.window_start for w in build(samples, rules)] == [MIDNIGHT]
    assert [w.window_start for w in build(samples, rules, day="2026-11-12")] == [MIDNIGHT - 120]


def test_empty_day(rules):
    assert build([], rules) == []


def test_load_day_reads_db(tmp_path, rules):
    import dataclasses

    from data.db import DbSink, connect, init_schema
    from tests.data.test_db import FakeSample

    conn = connect(tmp_path / "a.db", "d" * 64)
    init_schema(conn)
    sink = DbSink(conn)
    for row in fill(NOON, 30) + fill(MIDNIGHT + 30 * 3600, 30):  # second batch is the next day
        sink.write_sample(FakeSample(**{k: row[k] for k in (f.name for f in dataclasses.fields(FakeSample))}))
    ws = load_day(conn, DAY, now=NOON + 3600, rules=rules)
    assert [w.window_start for w in ws] == [NOON] and ws[0].category == "Coding"


def _with_media(rows, value):
    return [{**r, "media_playing": value} for r in rows]


def test_idle_while_video_plays_is_not_away(rules):
    rows = _with_media(fill(NOON, 30, bundle_id="com.google.Chrome", domain="youtube.com", idle_s=200.0, key=0), 1)
    [w] = build(rows, rules)
    assert not w.is_away and w.idle_seconds == 0 and w.media_frac == 1.0 and w.category == "Video"


def test_paused_video_idle_is_away(rules):
    rows = _with_media(fill(NOON, 30, bundle_id="com.google.Chrome", domain="youtube.com", idle_s=200.0, key=0), 0)
    [w] = build(rows, rules)
    assert w.is_away and w.media_frac == 0.0


def test_video_for_part_of_window(rules):
    rows = fill(NOON, 30, idle_s=200.0, key=0)
    rows = _with_media(rows[:20], 1) + _with_media(rows[20:], 0)  # 80 s watching, then 40 s idle
    [w] = build(rows, rules)
    assert w.idle_seconds == pytest.approx(40, abs=0.01) and not w.is_away
    assert w.media_frac == pytest.approx(20 / 30)


def test_media_unmeasured_keeps_old_behaviour(rules):
    [w] = build(fill(NOON, 30, idle_s=200.0, key=0), rules)  # rows without the column (pre-v2 data)
    assert w.is_away and w.media_frac is None


def test_locked_with_media_is_still_away(rules):
    rows = _with_media(fill(NOON, 30, idle_s=200.0, key=0, locked=1), None)
    [w] = build(rows, rules)
    assert w.is_away
