from common.model_api import LiveModel
from baselines.rules import RulesBaseline
from labels.config import load_labels_config
from tests.labels.test_autolabel import wins

W, SB, DR, RH = 0, 1, 2, 3


def run(pattern, hops=None):
    m = RulesBaseline(load_labels_config())
    return [m.step(w) for w in wins(pattern, hops)]


def test_is_a_live_model():
    assert isinstance(RulesBaseline(load_labels_config()), LiveModel)


def test_working_has_zero_risk():
    r = run("WWW")
    assert all(x.risk == 0.0 and x.top_state == W for x in r)


def test_risk_grows_with_session_length_and_hits_one_at_rabbit_min():
    r = run("W" + "D" * 12)
    risks = [x.risk for x in r[1:]]
    assert risks == sorted(risks) and risks[0] > 0
    assert r[10].risk == 1.0 and r[10].top_state == RH  # 10th distraction window = 20 min
    assert r[3].top_state == SB  # 3 windows in: still looks like a short break


def test_gray_zone_reads_as_drifting():
    assert run("W" + "D" * 7)[7].top_state == DR


def test_hops_trigger_rabbit_hole_early():
    r = run("WDDD", hops="Wabc")
    assert r[3].risk == 1.0 and r[3].top_state == RH


def test_past_only_return_to_work_resets():
    r = run("WDDDWWD")
    assert r[5].risk == 0.0 and r[6].risk < r[3].risk


def test_blip_does_not_reset_session():
    r = run("W" + "DDDDD" + "W" + "DDDD")
    assert r[6].top_state != W or r[6].risk > 0  # blip keeps session alive
    assert r[10].risk == 1.0  # 10 windows incl. blip


def test_away_carries_previous_readout_flagged():
    r = run("WDDDA")
    assert r[4].away and r[4].risk == r[3].risk


def test_away_ends_session():
    r = run("WDDDADD")
    assert r[6].risk < r[3].risk


def test_neutral_only_stretch_has_no_risk():
    assert run("WNNNNNNNNNNN")[-1].risk == 0.0


def test_reset():
    m = RulesBaseline(load_labels_config())
    for w in wins("W" + "D" * 9):
        m.step(w)
    m.reset()
    assert m.step(wins("D")[0]).risk < 0.5


def test_long_neutral_stretch_ends_live_session():
    r = run("WDDD" + "N" * 30)
    assert r[-1].risk == 0.0 and max(x.risk for x in r) < 0.5
