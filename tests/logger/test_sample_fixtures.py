from tests.fixtures.sample_cases import JSON_PATH, render


def test_sample_cases_json_is_current():
    assert JSON_PATH.exists(), "run: uv run python -m tests.fixtures.sample_cases"
    assert JSON_PATH.read_text() == render(), "stale fixture; run: uv run python -m tests.fixtures.sample_cases"
