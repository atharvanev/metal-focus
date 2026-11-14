import stat

from common.paths import data_dir, db_path


def test_data_dir_honors_env(tmp_path, monkeypatch):
    home = tmp_path / "mf"
    monkeypatch.setenv("METAL_FOCUS_HOME", str(home))
    assert db_path() == home / "activity.db"
    assert stat.S_IMODE(data_dir().stat().st_mode) == 0o700
