"""Where metal-focus keeps its on-disk data."""

import os
from pathlib import Path

_DEFAULT_HOME = Path.home() / "Library" / "Application Support" / "metal-focus"


def data_dir() -> Path:
    """Return the data directory (``$METAL_FOCUS_HOME`` or the default), creating it with mode 0700."""
    d = Path(os.environ.get("METAL_FOCUS_HOME", _DEFAULT_HOME))
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)
    return d


def db_path() -> Path:
    return data_dir() / "activity.db"
