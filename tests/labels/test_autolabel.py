from dataclasses import replace

import pytest

from data.windows import Window
from labels.autolabel import auto_label
from labels.config import load_labels_config

CAT = {"W": "Coding", "D": "Video", "N": "Other", "A": "Other"}


def wins(pattern: str, hops: str | None = None) -> list[Window]:
    """One window per char: W=work, D=distraction, N=neutral, A=away. `hops` gives each D its site."""
    out = []
    for i, ch in enumerate(pattern):
        out.append(Window(
            window_start=1000.0 + 120 * i, tz_offset_min=0, day="2026-11-13", category=CAT[ch],
            switch_count=0, idle_seconds=0.0, covered_seconds=120.0, is_away=ch == "A",
            typing_frac=None, click_frac=None, scroll_frac=None,
            dominant=(hops[i] if hops else "youtube.com") if ch == "D" else None, low_confidence=False))
    return out


@pytest.fixture(scope="module")
def cfg():
    return load_labels_config()


def lab(pattern, cfg, hops=None):
    short = {"Working": "W", "Short break": "S", "Drifting": "d", "Rabbit hole": "R", "Away": "A", None: "."}
    return "".join(short[x] for x in auto_label(wins(pattern, hops), cfg))


def test_config_defaults(cfg):
    assert (cfg.BREAK_MAX, cfg.RABBIT_MIN, cfg.HOPS_MIN, cfg.GAP_OK) == (5, 10, 3, 1)
    assert cfg.work_categories == ("Coding", "Docs")


def test_short_break(cfg):
    assert lab("WWDDDWW", cfg) == "WWSSSWW"


def test_break_max_boundary(cfg):
    assert lab("W" + "D" * 5 + "W", cfg) == "W" + "S" * 5 + "W"
    assert lab("W" + "D" * 6 + "W", cfg) == "W" + "." * 6 + "W"  # gray zone 6-9 windows: unlabeled


def test_long_session_is_rabbit_hole_with_drifting_lead_in(cfg):
    assert lab("W" + "D" * 10 + "W", cfg) == "W" + "ddd" + "R" * 7 + "W"
    assert lab("W" + "D" * 6 + "D" * 6 + "W", cfg) == "W" + "ddd" + "R" * 9 + "W"


def test_drifting_is_a_third_capped(cfg):
    out = lab("W" + "D" * 4 + "W", cfg, hops="Wabc" + "c" + "W")  # 4 windows, 3 sites -> hoppy
    assert out == "WdRRRW"


def test_hops_make_a_short_session_a_rabbit_hole(cfg):
    assert lab("WDDDW", cfg, hops="WabcW") == "WdRRW"


def test_one_window_blip_does_not_end_session(cfg):
    p = "W" + "DDDDD" + "W" + "DDDDD" + "WW"
    assert lab(p, cfg) == "W" + "dddRRRRRRRR" + "WW"


def test_two_window_return_ends_session(cfg):
    assert lab("WDDDWWDDDW", cfg) == "WSSSWWSSSW"


def test_cut_off_by_end_of_day_unlabeled_unless_already_rabbit(cfg):
    assert lab("WWDDD", cfg) == "WW..."
    assert lab("W" + "D" * 10, cfg) == "W" + "ddd" + "R" * 7


def test_away_is_away_and_ends_session(cfg):
    assert lab("WDDAAW", cfg) == "W..AAW"
    assert lab("WDDDDDDDDDDAW", cfg) == "WdddRRRRRRRAW"


def test_neutral_only_session_unlabeled(cfg):
    assert lab("WNNNNNNNNNNNW", cfg) == "W" + "." * 11 + "W"


def test_long_neutral_stretch_ends_session_and_is_never_a_rabbit_hole(cfg):
    # An hour of Excluded (e.g. confidential work) after a distraction must not become a rabbit hole.
    assert lab("WD" + "N" * 30 + "DW", cfg) == "W." + "." * 30 + "SW"


def test_one_window_neutral_blip_does_not_end_session(cfg):
    assert lab("W" + "DDDDD" + "N" + "DDDD" + "W", cfg) == "W" + "dddRRRRRRR" + "W"


def test_session_ended_by_neutral_is_not_a_return_to_work(cfg):
    assert lab("WDDDNNW", cfg) == "W.....W"


def test_thresholds_come_from_config(cfg):
    assert lab("W" + "D" * 6 + "W", replace(cfg, BREAK_MAX=6)) == "W" + "S" * 6 + "W"


def _spotify_run(pattern: str) -> list[Window]:
    """S = Spotify window still neutral (Other), X = Spotify window escalated to Social."""
    base = wins(pattern.replace("S", "N").replace("X", "D"))
    return [replace(w, dominant="com.spotify.client", category="Social" if ch == "X" else w.category)
            if ch in "SX" else w for w, ch in zip(base, pattern)]


def test_escalated_spotify_lead_in_counts_as_distraction(cfg):
    # 4 min work, 8 min YouTube, 12 min Spotify (first 2 windows still Other), back to work:
    # one 10-window session -> Drifting then Rabbit hole, not a Short break.
    ws = _spotify_run("WW" + "DDDD" + "SSXXXX" + "WW")
    short = {"Working": "W", "Short break": "S", "Drifting": "d", "Rabbit hole": "R", None: "."}
    assert "".join(short[x] for x in auto_label(ws, cfg)) == "WW" + "ddd" + "R" * 7 + "WW"


def test_spotify_that_never_escalates_stays_neutral(cfg):
    ws = _spotify_run("WW" + "SS" + "WW")
    assert auto_label(ws, cfg)[2:4] == [None, None]
