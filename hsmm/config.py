"""Load hsmm/config.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).with_name("config.yaml")

WORKING, SHORT_BREAK, DRIFTING, RABBIT_HOLE = range(4)


@lru_cache(maxsize=None)
def load_config(path: str | None = None) -> dict:
    with open(path or CONFIG_PATH) as f:
        return yaml.safe_load(f)
