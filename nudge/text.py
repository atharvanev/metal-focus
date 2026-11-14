"""Nudge message text from the current distraction session's apps/domains.

Privacy: reads only samples(ts, app_name, domain, excluded, private, locked);
skips excluded, private and locked rows and rows with neither app nor domain;
never reads window titles.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Sequence


def work_categories(cfg: dict) -> tuple[str, ...]:
    try:  # labels/ owns the canonical list once it lands
        from labels.config import load_labels_config  # type: ignore[import-not-found]

        return tuple(load_labels_config().work_categories)
    except ImportError:
        return tuple(cfg["work_categories"])


def session_start_index(
    windows: Sequence[Any], work: tuple[str, ...], distraction: tuple[str, ...], gap_ok: int = 1
) -> int:
    """Index of the first window of the distraction session ending at windows[-1].

    Only distraction categories (Chat, Video, Social) extend the session; up to
    `gap_ok` consecutive work windows are tolerated as a blip. Everything else
    is a boundary: Away, `Excluded` (e.g. confidential work apps, which must
    never be swept into a session) and `Other`. The session's start becomes
    nudge_responses.window_start, and a "Back to work" answer hard-labels that
    whole range, so erring short is the safe direction.
    """
    i = len(windows) - 1
    start = i
    blip = 0
    while i >= 0:
        w = windows[i]
        if getattr(w, "is_away", False):
            break
        if w.category in work:
            blip += 1
            if blip > gap_ok:
                break
        elif w.category in distraction:
            blip = 0
            start = i
        else:  # Excluded, Other, unknown
            break
        i -= 1
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
    work = work_categories(cfg)
    ws = cfg["window_s"]
    s = session_start_index(windows, work, tuple(cfg["distraction_categories"]))
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
