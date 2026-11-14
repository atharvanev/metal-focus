import json
from pathlib import Path

import pytest

from logger.media import frontmost_plays_media

FIXTURE = json.loads((Path(__file__).parents[1] / "fixtures" / "media_cases.json").read_text())


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda c: c["name"])
def test_media_cases(case):
    paths = {int(k): v for k, v in FIXTURE["paths"].items()}
    got = frontmost_plays_media(case["assertions"], FIXTURE["front_pid"], FIXTURE["front_bundle_path"], paths.get)
    assert got is case["expected"]


def test_no_bundle_path_matches_pid_only():
    a = [{"AssertPID": 101, "AssertType": "PreventUserIdleDisplaySleep"}]
    assert frontmost_plays_media(a, 101, None, lambda pid: "/x") is True
    assert frontmost_plays_media(a, 100, None, lambda pid: "/x") is False
