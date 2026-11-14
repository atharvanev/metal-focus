"""Contract check: windows from data.windows feed the HSMM unchanged."""

from data.windows import build_windows
from hsmm.io import window_to_obs
from hsmm.live import LiveHSMM
from hsmm.simulate import default_true_params
from labels.categorizer import load_rules
from tests.data.test_windows import DAY, NOON, fill


def test_windows_step_live_hsmm():
    rules = load_rules()
    samples = (fill(NOON, 30) + fill(NOON + 120, 30, bundle_id="com.google.Chrome", domain="reddit.com", switches=1)
               + fill(NOON + 240, 30, bundle_id="com.excluded", excluded=1))
    ws = build_windows(samples, day=DAY, now=NOON + 3600, rules=rules)
    model = LiveHSMM(default_true_params())
    obs = [window_to_obs(w, model.cfg) for w in ws]
    assert obs[0][0] >= 0 and obs[1][0] >= 0  # Coding, Social -> real category indices
    assert obs[2][0] == -1  # Excluded -> missing observation
    readouts = [model.step(w) for w in ws]
    assert all(0.0 <= r.risk <= 1.0 for r in readouts)
