"""Nudge message text from the current distraction session's apps/domains.

Privacy: reads only samples(ts, app_name, domain, excluded, private, locked);
skips excluded, private and locked rows and rows with neither app nor domain;
never reads window titles.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Sequence

from labels.autolabel import window_kinds
from labels.config import load_labels_config


def session_start_index(kinds: Sequence[str], gap_ok: int = 1) -> int:
    """Index of the first window of the distraction session ending at the last window.

    Same rule as labels/autolabel.py (agreed with metal-focus-d1): only
    distraction windows (D) start/extend a session; Away (A) always ends it; a
    run of <= gap_ok work/neutral windows (W/N, where N = Other/Excluded) between
    two distraction windows is a blip inside the session; a longer run ends it.
    So an Excluded *stretch* (confidential work) is never swept into a session.
    The start becomes nudge_responses.window_start, which a "Back to work"
    answer hard-labels.
    """
    n = len(kinds)
    start = n - 1
    j = n - 1
    while j >= 0:
        k = kinds[j]
        if k == "D":
            start = j
            j -= 1
        elif k == "A":
            break
        else:  # run of W/N windows
            v = j
            while v >= 0 and kinds[v] in "WN":
                v -= 1
            # short run preceded by distraction: a blip (or, at the very end, a blip so far)
            if j - v <= gap_ok and v >= 0 and kinds[v] == "D":
                j = v
            else:
                break
    return start


def _label(row) -> str | None:
    app, domain = row
    return domain or app


def _sample_labels(conn, t0: float, t1: float) -> list[str]:
    try:
        rows = conn.execute(
            "SELECT app_name, domain FROM samples WHERE ts >= ? AND ts < ? "
            "AND excluded = 0 AND COALESCE(private, 0) = 0 AND COALESCE(locked, 0) = 0 "
            "AND (app_name IS NOT NULL OR domain IS NOT NULL) ORDER BY ts",
            (t0, t1),
        ).fetchall()
    except Exception:  # samples table missing (tests, fresh DB)
        return []
    return [lab for lab in (_label(r) for r in rows) if lab]


def hops(labels: Sequence[str], max_hops: int) -> list[str]:
    out: list[str] = []
    for lab in labels:
        if not out or out[-1] != lab:
            out.append(lab)
    return out[-max_hops:]


def build_body(conn, windows: Sequence[Any], cfg: dict) -> tuple[str, float]:
    """(message body, session start epoch s) for the session ending at windows[-1]."""
    lc = load_labels_config()  # labels/ owns categories, so live nudges and hindsight labels agree on sessions
    work = lc.work_categories
    ws = cfg["window_s"]
    s = session_start_index(window_kinds(windows, lc), lc.GAP_OK)
    t0 = float(windows[s].window_start)
    t1 = float(windows[-1].window_start) + ws
    minutes = round((t1 - t0) / 60)
    chain = hops(_sample_labels(conn, t0, t1), cfg["max_hops"])

    # last work context: most common app/domain in the latest work window before the session
    ctx = None
    for w in reversed(windows[:s]):
        if w.category in work and not getattr(w, "is_away", False):
            labs = _sample_labels(conn, float(w.window_start), float(w.window_start) + ws)
            if labs:
                ctx = Counter(labs).most_common(1)[0][0]
            break

    what = " -> ".join(chain) if chain else "off task"
    body = f"This looks like a rabbit hole: {what}, {minutes} min in."
    body += f" Head back to {ctx}?" if ctx else " Time to head back?"
    return body, t0
