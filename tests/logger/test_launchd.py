import plistlib
from pathlib import Path

from logger.launchd import LABEL, render_plist, venv_python


def _plist(python=Path("/repo/.venv/bin/python")):
    return plistlib.loads(render_plist(python, Path("/repo"), Path("/logs")))


def test_plist_fields():
    p = _plist()
    assert p["Label"] == LABEL == "com.metalfocus.logger"
    assert p["ProgramArguments"][1:] == ["-m", "logger", "run"]
    assert p["KeepAlive"] == {"SuccessfulExit": False}
    assert p["RunAtLoad"] is True
    assert p["WorkingDirectory"] == "/repo"
    assert p["StandardErrorPath"] == "/logs/logger.err.log"


def test_plist_uses_given_python():
    assert _plist(Path("/x/bin/python"))["ProgramArguments"][0] == "/x/bin/python"


def test_venv_python_is_not_resolved_past_the_venv(tmp_path):
    # Resolving the symlink would point at the bare uv interpreter and lose the venv's packages.
    py = venv_python(tmp_path)
    assert py == tmp_path / ".venv" / "bin" / "python"
