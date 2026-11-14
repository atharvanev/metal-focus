"""End-to-end on the real schema and real windowing: samples -> load_day -> LiveRunner -> nudge engine."""

import json
import sqlite3
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

from data.windows import load_day
from hsmm.runner import LiveRunner
from nudge.engine import on_tick
from nudge.rule import load_config

SCHEMA = (Path(__file__).resolve().parents[2] / "data" / "schema.sql").read_text()
DAY = "2026-11-13"
T0 = datetime(2026, 11, 13, 14, 0, tzinfo=timezone.utc).timestamp()  # tz_offset 0 -> local == UTC


def _insert(conn, t, bundle, domain=None, excluded=0, switches=0):
    conn.execute(
        "INSERT INTO samples (ts, tz_offset_min, dt, bundle_id, app_name, window_title, domain, private, excluded,"
        " fullscreen, locked, idle_s, app_switches, key_active, click_active, scroll_active)"
        " VALUES (?, 0, 4.0, ?, ?, NULL, ?, 0, ?, 0, 0, 1.0, ?, 1, 0, 0)",
        (t, bundle, bundle.split(".")[-1], domain, excluded, switches),
    )


def _build_day(conn):
    t = T0
    for _ in range(30 * 6):  # 6 min of coding (30 samples / window)
        _insert(conn, t, "com.microsoft.VSCode")
        t += 4
    for _ in range(30 * 4):  # 8 min of confidential work: Excluded
        _insert(conn, t, "com.ibm.secret", excluded=1)
        t += 4
    for i in range(30 * 16):  # 32 min of YouTube <-> Reddit hopping
        dom = "youtube.com" if (i // 45) % 2 == 0 else "reddit.com"
        _insert(conn, t, "com.apple.Safari", domain=dom, switches=1 if i % 15 == 0 else 0)
        t += 4
    conn.commit()
    return t


def test_runner_and_engine_on_real_windowing():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)  # includes nudge_responses + notify_outbox
    end = _build_day(conn)
    cfg = {**load_config(), "holdout_frac": 0.0}

    decisions = []
    runner = LiveRunner(conn, partial(load_day, now=end + 1))
    # replay the day window by window, as the live loop would see it
    wins_all = load_day(conn, DAY, now=end + 1)
    assert [w.category for w in wins_all[:3]] == ["Coding"] * 3
    assert any(w.category == "Excluded" for w in wins_all)
    for k in range(1, len(wins_all) + 1):
        prefix = wins_all[:k]
        runner.load_day = lambda c, d, p=prefix: p
        rows = runner.tick(DAY)
        now = prefix[-1].window_start + 125
        res = on_tick(conn, DAY, rows, prefix, now=now, cfg=cfg)
        if res and "nudge_id" in res:
            decisions.append((k, res))

    n_readouts = conn.execute("SELECT COUNT(*) FROM readouts WHERE model='hsmm'").fetchone()[0]
    assert n_readouts == len(wins_all)
    assert decisions, "a 32-min YouTube/Reddit stretch should trigger a nudge"
    _, first = decisions[0]
    body = first["body"]
    assert "youtube.com" in body or "reddit.com" in body
    assert "secret" not in body and "ibm" not in body.lower()
    # work context skips the Excluded stretch and names the last non-confidential work app
    assert body.endswith("Head back to VSCode?")

    nr = conn.execute("SELECT window_start, window_end FROM nudge_responses ORDER BY id").fetchall()
    first_video = next(w.window_start for w in wins_all if w.category in ("Video", "Social"))
    assert nr[0][0] == first_video  # session never reaches back into the Excluded stretch
    payload = json.loads(conn.execute("SELECT payload_json FROM notify_outbox ORDER BY id").fetchone()[0])
    assert payload["body"] == body
    # cooldown: decisions at least 10 windows apart
    ks = [k for k, _ in decisions]
    assert all(b - a >= 10 for a, b in zip(ks, ks[1:]))
