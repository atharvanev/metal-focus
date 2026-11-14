from pathlib import Path

import pytest

from logger.config import load_config


def _write(p: Path, text: str) -> Path:
    p.write_text(text)
    return p


def test_defaults_load():
    cfg = load_config()
    assert cfg.sample_interval_s == 4
    assert "com.1password.1password" in cfg.exclude_bundle_ids
    assert cfg.browsers["com.google.Chrome"] == "chromium"
    assert cfg.unknown_private_policy == "drop"


def test_local_overrides_append_lists(tmp_path):
    local = _write(tmp_path / "local.yaml", "sample_interval_s: 3\nexclude:\n  domains: [chase.com]\n")
    cfg = load_config(local_path=local)
    assert cfg.sample_interval_s == 3
    assert cfg.exclude_domains[0] == "ibm.com"
    assert "chase.com" in cfg.exclude_domains


def test_missing_local_is_fine(tmp_path):
    cfg = load_config(local_path=tmp_path / "nope.yaml")
    assert cfg.sample_interval_s == 4


@pytest.mark.parametrize("bad", ["sample_interval_s: 0\n", "unknown_private_policy: maybe\n"])
def test_invalid_values_rejected(tmp_path, bad):
    with pytest.raises(ValueError):
        load_config(local_path=_write(tmp_path / "local.yaml", bad))


def test_title_patterns_case_insensitive(tmp_path):
    local = _write(tmp_path / "local.yaml", "exclude:\n  title_patterns: [secret-repo]\n")
    cfg = load_config(local_path=local)
    assert any(p.search("SECRET-REPO — VS Code") for p in cfg.exclude_title_patterns)


def test_financial_always_loaded(tmp_path):
    local = _write(tmp_path / "local.yaml", "exclude:\n  bundle_ids: []\n  domains: []\n  title_patterns: []\n")
    cfg = load_config(local_path=local)
    assert "chase.com" in cfg.exclude_domains
    assert "bank" in cfg.exclude_domain_keywords
    assert "com.apple.stocks" in cfg.exclude_bundle_ids
