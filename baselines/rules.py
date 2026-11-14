"""Rules baseline: the auto-labeler's rules run live (past-only), as a common.model_api.LiveModel.

Per window it tracks the open distraction session (same definition as labels/autolabel.py: non-work,
non-Away windows, work blips of <= GAP_OK windows allowed) and reports:
- risk = max(L / RABBIT_MIN, (H - 1) / (HOPS_MIN - 1)), capped at 1, where L is the session length in
  windows and H the number of distinct distraction apps/sites. Risk is 0 with no session, or with a
  session of only neutral (Other/Excluded) windows.
- a one-hot state: Working (no session), Short break (L <= BREAK_MAX), Drifting (gray zone), Rabbit
  hole (L >= RABBIT_MIN or H >= HOPS_MIN).
A first work window after a session can't yet be told apart from a blip, so the session readout is
carried for it. Away windows carry the previous readout with away=True and end the session.
"""

from __future__ import annotations

from typing import Any

from common.model_api import Readout
from labels.config import LabelsConfig, load_labels_config

WORKING, SHORT_BREAK, DRIFTING, RABBIT_HOLE = range(4)


class RulesBaseline:
    name = "rules"

    def __init__(self, cfg: LabelsConfig | None = None):
        self.cfg = cfg or load_labels_config()
        self.reset()

    def reset(self) -> None:
        self.length = 0  # windows in the open session (incl. blips); 0 = no session
        self.work_run = 0
        self.sites: set[str] = set()
        self.has_distraction = False
        self.last: Readout | None = None

    def _readout(self, state: int, risk: float, minutes: float) -> Readout:
        p = tuple(1.0 if i == state else 0.0 for i in range(4))
        return Readout(risk=risk, p_state=p, top_state=state, top_prob=1.0, minutes_in_state=minutes)

    def _session_readout(self) -> Readout:
        c = self.cfg
        if not self.has_distraction:
            return self._readout(WORKING, 0.0, 0.0)
        hops = len(self.sites)
        risk = min(1.0, max(self.length / c.RABBIT_MIN, (hops - 1) / (c.HOPS_MIN - 1)))
        if self.length >= c.RABBIT_MIN or hops >= c.HOPS_MIN:
            state = RABBIT_HOLE
        elif self.length <= c.BREAK_MAX:
            state = SHORT_BREAK
        else:
            state = DRIFTING
        return self._readout(state, risk, self.length * 2.0)

    def _end_session(self) -> None:
        self.length, self.work_run, self.sites, self.has_distraction = 0, 0, set(), False

    def step(self, window: Any) -> Readout:
        c = self.cfg
        if getattr(window, "is_away", False):
            self._end_session()
            base = self.last or self._readout(WORKING, 0.0, 0.0)
            return Readout(**{**base.__dict__, "away": True})
        if window.category in c.work_categories:
            if self.length and self.work_run < c.GAP_OK:
                self.work_run += 1
                self.last = self._session_readout()  # might still be a blip
            else:
                self._end_session()
                self.last = self._readout(WORKING, 0.0, 0.0)
            return self.last
        self.length += self.work_run + 1
        self.work_run = 0
        if window.category in c.distraction_categories:
            self.has_distraction = True
            if getattr(window, "dominant", None):
                self.sites.add(window.dominant)
        self.last = self._session_readout()
        return self.last
