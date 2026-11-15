"""Rules categorizer: (bundle id, domain, title) -> Categorization(category, low_confidence).

Categories are Coding, Docs, Chat, Video, Social, plus Other (no rule matched) and Excluded
(privacy-excluded rows). Rules live in labels/categories.yaml; never hard-code them here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import yaml

from logger.privacy import domain_matches

_HERE = Path(__file__).parent
DEFAULT_PATH = _HERE / "categories.yaml"
LOCAL_PATH = _HERE / "categories.local.yaml"
CATEGORIES = ("Coding", "Docs", "Chat", "Video", "Social")


class Categorization(NamedTuple):
    category: str
    low_confidence: bool


@dataclass(frozen=True)
class Rules:
    bundle_ids: dict[str, str]
    bundle_prefixes: tuple[tuple[str, str], ...]
    domains: tuple[tuple[str, str], ...]
    low_bundle_ids: frozenset[str]
    low_domains: tuple[str, ...]
    lecture_category: str
    lecture_domains: tuple[str, ...]
    lecture_title: re.Pattern | None
    neutral_bundle_ids: frozenset[str] = frozenset()
    neutral_domains: tuple[str, ...] = ()
    escalate_category: str | None = None
    escalate_after: int = 0
    escalate_bundle_ids: frozenset[str] = frozenset()
    escalate_domains: tuple[str, ...] = ()


def _merge(base: dict, local: dict, key: str) -> dict[str, list]:
    out: dict[str, list] = {}
    for src in (base.get(key) or {}, local.get(key) or {}):
        for cat, items in src.items():
            out.setdefault(cat, []).extend(items or [])
    return out


def load_rules(path: Path | None = None, local_path: Path | None = None) -> Rules:
    base = yaml.safe_load((path or DEFAULT_PATH).read_text()) or {}
    lp = local_path or LOCAL_PATH
    local = (yaml.safe_load(lp.read_text()) or {}) if lp.exists() else {}
    for key in ("bundle_ids", "bundle_prefixes", "domains"):
        for cat in _merge(base, local, key):
            if cat not in CATEGORIES:
                raise ValueError(f"{key}: unknown category {cat!r}")
    low = {k: list((base.get("low_confidence") or {}).get(k) or []) + list((local.get("low_confidence") or {}).get(k) or [])
           for k in ("bundle_ids", "domains")}
    def lists(key: str, sub: str) -> list[str]:
        return [x for src in (base, local) for x in ((src.get(key) or {}).get(sub) or [])]

    esc = {**(base.get("escalate") or {}), **{k: v for k, v in (local.get("escalate") or {}).items()
                                              if k in ("category", "after_windows")}}
    esc_cat = esc.get("category")
    if esc_cat is not None and esc_cat not in CATEGORIES:
        raise ValueError(f"escalate: unknown category {esc_cat!r}")
    lec = base.get("lecture") or {}
    kws = list(lec.get("title_keywords") or [])
    return Rules(
        bundle_ids={b: c for c, bs in _merge(base, local, "bundle_ids").items() for b in bs},
        bundle_prefixes=tuple((p, c) for c, ps in _merge(base, local, "bundle_prefixes").items() for p in ps),
        domains=tuple((d.lower(), c) for c, ds in _merge(base, local, "domains").items() for d in ds),
        low_bundle_ids=frozenset(low["bundle_ids"]),
        low_domains=tuple(d.lower() for d in low["domains"]),
        lecture_category=lec.get("category", "Docs"),
        lecture_domains=tuple(lec.get("domains") or []),
        lecture_title=re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, kws)) + r")(?!\w)", re.I) if kws else None,
        neutral_bundle_ids=frozenset(lists("neutral", "bundle_ids")),
        neutral_domains=tuple(d.lower() for d in lists("neutral", "domains")),
        escalate_category=esc_cat,
        escalate_after=int(esc.get("after_windows") or 0),
        escalate_bundle_ids=frozenset(lists("escalate", "bundle_ids")),
        escalate_domains=tuple(d.lower() for d in lists("escalate", "domains")),
    )


def is_escalating(dominant: str | None, rules: Rules) -> bool:
    """True if a window's dominant app/site is on the escalate list (e.g. Spotify)."""
    if not dominant:
        return False
    return dominant in rules.escalate_bundle_ids or any(domain_matches(dominant, d) for d in rules.escalate_domains)


def categorize(
    bundle_id: str | None, domain: str | None, title: str | None, rules: Rules, excluded: bool = False
) -> Categorization:
    if excluded:
        return Categorization("Excluded", False)
    if domain:
        if title and rules.lecture_title and any(domain_matches(domain, d) for d in rules.lecture_domains):
            if rules.lecture_title.search(title):
                return Categorization(rules.lecture_category, False)
        for d, cat in rules.domains:
            if domain_matches(domain, d):
                return Categorization(cat, any(domain_matches(domain, x) for x in rules.low_domains))
        known = any(domain_matches(domain, d) for d in rules.neutral_domains + rules.escalate_domains)
        return Categorization("Other", not known)
    if bundle_id:
        cat = rules.bundle_ids.get(bundle_id)
        if cat is None:
            cat = next((c for p, c in rules.bundle_prefixes if bundle_id.startswith(p)), None)
        if cat is not None:
            return Categorization(cat, bundle_id in rules.low_bundle_ids)
        if bundle_id in rules.neutral_bundle_ids or bundle_id in rules.escalate_bundle_ids:
            return Categorization("Other", False)
    return Categorization("Other", True)
