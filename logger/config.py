"""Logger configuration: committed defaults + built-in financial list + optional local overrides."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

_HERE = Path(__file__).parent
DEFAULT_PATH = _HERE / "config.yaml"
LOCAL_PATH = _HERE / "config.local.yaml"
FINANCIAL_PATH = _HERE / "financial.yaml"


@dataclass(frozen=True)
class LoggerConfig:
    sample_interval_s: float
    max_gap_s: float
    browser_timeout_s: float
    browsers: dict[str, str]
    unknown_private_policy: Literal["drop", "keep"]
    exclude_bundle_ids: frozenset[str]
    exclude_domains: tuple[str, ...]
    exclude_title_patterns: tuple[re.Pattern, ...]
    exclude_domain_keywords: tuple[str, ...]


def _read(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def _whole_word(fragment: str) -> re.Pattern:
    return re.compile(rf"(?<!\w)(?:{fragment})(?!\w)", re.IGNORECASE)


def load_config(
    path: Path | None = None,
    local_path: Path | None = None,
    financial_path: Path | None = None,
) -> LoggerConfig:
    base = _read(path or DEFAULT_PATH)
    local = _read(local_path or LOCAL_PATH)
    fin = _read(financial_path or FINANCIAL_PATH)

    def scalar(key: str) -> Any:
        return local.get(key, base.get(key))

    def excl(key: str) -> list:
        return list((base.get("exclude") or {}).get(key) or []) + list((local.get("exclude") or {}).get(key) or [])

    interval = float(scalar("sample_interval_s"))
    max_gap = float(scalar("max_gap_s"))
    timeout = float(scalar("browser_timeout_s"))
    policy = scalar("unknown_private_policy")
    if interval <= 0 or max_gap <= interval or timeout <= 0:
        raise ValueError("sample_interval_s, browser_timeout_s must be > 0 and max_gap_s > sample_interval_s")
    if policy not in ("drop", "keep"):
        raise ValueError(f"unknown_private_policy must be 'drop' or 'keep', got {policy!r}")

    browsers = dict(base.get("browsers") or {})
    browsers.update(local.get("browsers") or {})
    for bid, kind in browsers.items():
        if kind not in ("safari", "chromium", "arc"):
            raise ValueError(f"browser {bid}: unknown kind {kind!r}")

    patterns = [re.compile(p, re.IGNORECASE) for p in excl("title_patterns")]
    patterns += [_whole_word(k) for k in fin.get("title_keywords") or []]

    return LoggerConfig(
        sample_interval_s=interval,
        max_gap_s=max_gap,
        browser_timeout_s=timeout,
        browsers=browsers,
        unknown_private_policy=policy,
        exclude_bundle_ids=frozenset(excl("bundle_ids") + list(fin.get("bundle_ids") or [])),
        exclude_domains=tuple(d.lower() for d in excl("domains") + list(fin.get("domains") or [])),
        exclude_title_patterns=tuple(patterns),
        exclude_domain_keywords=tuple(k.lower() for k in fin.get("domain_keywords") or []),
    )
