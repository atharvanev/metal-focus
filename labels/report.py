"""Where is time still uncategorized? Read-only report with strict category suggestions.

    uv run python -m labels.report [--days 7] [--top 15] [--json] [--no-titles] [--no-suggest]

Per sample (not per window) so time goes to the exact app/site. "unknown" = Other with no matching
rule; "neutral" = Other on the neutral/escalate lists; "unsure" = a real category flagged
low-confidence (these drive ambiguous-app check-ins). Suggestions (labels/embed.py) need
``uv sync --group embed``. Never writes anything: output goes to stdout only.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable

from labels.categorizer import Rules, categorize, load_rules

TITLE_MAX = 50


@dataclass(frozen=True)
class KeyStat:
    key: str
    seconds: float
    title: str | None
    category: str
    low_confidence: bool


@dataclass(frozen=True)
class Report:
    total_s: float
    by_category: dict[str, float]
    unknown: list[KeyStat]
    unsure: list[KeyStat]
    refs: list[tuple[str, str]]  # (key + title, label) for rule-labelled, high-confidence text


def _bucket(category: str, low: bool) -> str:
    return ("unknown" if low else "neutral") if category == "Other" else category


def build_report(samples: Iterable[dict], rules: Rules, max_refs_per_category: int = 200) -> Report:
    by_cat: dict[str, float] = defaultdict(float)
    keys: dict[tuple[str, str, bool], float] = defaultdict(float)
    titles: dict[str, Counter] = defaultdict(Counter)
    ref_time: dict[tuple[str, str], float] = defaultdict(float)
    for r in samples:
        if r["locked"]:
            continue
        dt = float(r["dt"])
        c = categorize(r["bundle_id"], r["domain"], r["window_title"], rules, excluded=bool(r["excluded"]))
        bucket = _bucket(c.category, c.low_confidence)
        by_cat[bucket] += dt
        key = None if r["excluded"] else (r["domain"] or r["bundle_id"])
        if key is None:
            continue
        keys[(key, c.category, c.low_confidence)] += dt
        if r["window_title"]:
            titles[key][r["window_title"]] += dt
            if bucket != "unknown" and not c.low_confidence:
                ref_time[(f"{key} {r['window_title']}", bucket)] += dt

    def stat(k: tuple[str, str, bool], secs: float) -> KeyStat:
        top = titles[k[0]].most_common(1)
        return KeyStat(k[0], secs, top[0][0][:TITLE_MAX] if top else None, k[1], k[2])

    ranked = sorted(keys.items(), key=lambda kv: -kv[1])
    unknown = [stat(k, v) for k, v in ranked if k[1] == "Other" and k[2]]
    unsure = [stat(k, v) for k, v in ranked if k[1] != "Other" and k[2]]
    per_label: dict[str, int] = defaultdict(int)
    refs = []
    for (text, label), _ in sorted(ref_time.items(), key=lambda kv: -kv[1]):
        if per_label[label] < max_refs_per_category:
            per_label[label] += 1
            refs.append((text, label))
    return Report(sum(by_cat.values()), dict(by_cat), unknown, unsure, refs)


def _rows(conn, since: float) -> list[dict]:
    cur = conn.execute("SELECT ts, dt, bundle_id, domain, window_title, excluded, locked FROM samples "
                       "WHERE ts >= ? ORDER BY ts", (since,))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _suggestions(rep: Report, top: int, err) -> dict[str, object]:
    from labels.embed import load_embed_config, load_minilm, suggest

    cfg = load_embed_config()
    try:
        emb = load_minilm(cfg)
    except ImportError as e:
        print(f"(no suggestions: {e})", file=err)
        return {}
    qs = rep.unknown[:top]
    out = suggest(emb, rep.refs, [f"{k.key} {k.title or ''}".strip() for k in qs], cfg)
    return {k.key: s for k, s in zip(qs, out)}


def _mins(s: float) -> str:
    return f"{s / 60:6.1f} min"


def main(argv: list[str] | None = None, conn=None, out=None, err=None) -> int:
    out, err = out or sys.stdout, err or sys.stderr
    p = argparse.ArgumentParser(prog="python -m labels.report", description=__doc__.splitlines()[0])
    p.add_argument("--days", type=float, default=7)
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--json", action="store_true", help="JSON lines instead of a table")
    p.add_argument("--no-titles", action="store_true")
    p.add_argument("--no-suggest", action="store_true")
    a = p.parse_args(argv)
    if conn is None:
        from data.db import open_existing

        conn = open_existing()
    rep = build_report(_rows(conn, time.time() - a.days * 86400), load_rules())
    if not rep.total_s:
        print(f"no samples in the last {a.days:g} days", file=out)
        return 0
    sugg = {} if a.no_suggest else _suggestions(rep, a.top, err)

    def title(k: KeyStat) -> str | None:
        return None if a.no_titles else k.title

    if a.json:
        print(json.dumps({"kind": "summary", "days": a.days, "total_min": round(rep.total_s / 60, 1),
                          "share": {c: round(v / rep.total_s, 3) for c, v in sorted(rep.by_category.items())}}), file=out)
        for kind, rows in (("unknown", rep.unknown), ("unsure", rep.unsure)):
            for k in rows[:a.top]:
                d = {"kind": kind, "key": k.key, "minutes": round(k.seconds / 60, 1), "title": title(k)}
                if kind == "unsure":
                    d["category"] = k.category
                elif k.key in sugg:
                    s = sugg[k.key]
                    d.update(suggested=s.category, score=s.score, runner_up=s.runner_up)
                print(json.dumps(d), file=out)
        return 0
    print(f"Last {a.days:g} days: {rep.total_s / 3600:.1f} h logged", file=out)
    print("  " + "  ".join(f"{c} {v / rep.total_s:.0%}" for c, v in sorted(rep.by_category.items(), key=lambda kv: -kv[1])), file=out)
    print(f"\nUnknown (no rule matched), top {a.top} by time", file=out)
    for k in rep.unknown[:a.top]:
        s = sugg.get(k.key)
        hint = "" if s is None else f"  -> {s.category or '?'} ({s.score:.2f})"
        print(f"  {_mins(k.seconds)}  {k.key:<32} {json.dumps(title(k), ensure_ascii=False) if title(k) else ''}{hint}", file=out)
    print("\nKnown but unsure (feeds ambiguous-app check-ins)", file=out)
    for k in rep.unsure[:a.top]:
        print(f"  {_mins(k.seconds)}  {k.key:<32} {k.category}", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
