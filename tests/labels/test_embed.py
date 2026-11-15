import math

import mlx.core as mx
import pytest

from labels.embed import EmbedConfig, Suggestion, load_embed_config, suggest

CFG = EmbedConfig(model="fake", k=5, min_score=0.40, min_margin=0.10, max_refs_per_category=200)


def unit(*xs):
    n = math.sqrt(sum(x * x for x in xs))
    return [x / n for x in xs]


class Fake:
    """Maps each known text to a fixed 3-d unit vector."""

    def __init__(self, table):
        self.table = table

    def embed(self, texts):
        return mx.array([self.table[t] for t in texts])


VEC = {
    "code1": unit(1, 0, 0), "code2": unit(1, 0.05, 0), "soc1": unit(0, 1, 0), "soc2": unit(0.05, 1, 0),
    "q_code": unit(1, 0.1, 0), "q_far": unit(0, 0, 1), "q_tie": unit(1, 1, 0), "q_docsish": unit(0, 0.2, 1),
}
REFS = [("code1", "Coding"), ("code2", "Coding"), ("soc1", "Social"), ("soc2", "Social")]


def test_clear_match_suggested():
    [s] = suggest(Fake(VEC), REFS, ["q_code"], CFG)
    assert s.category == "Coding" and s.score >= 0.4 and s.runner_up == "Social"


def test_low_score_is_none():
    [s] = suggest(Fake(VEC), REFS, ["q_far"], CFG)
    assert s.category is None


def test_near_tie_is_none():
    [s] = suggest(Fake(VEC), REFS, ["q_tie"], CFG)
    assert s.category is None and s.score >= 0.4  # strong but ambiguous -> strict: no suggestion


def test_unreferenced_category_never_suggested():
    # q_docsish is closest to nothing we have examples for; Docs has no refs so it can't win
    out = suggest(Fake(VEC), REFS, ["q_docsish", "q_code"], CFG)
    assert all(s.category in (None, "Coding", "Social") for s in out)


def test_no_refs_all_none():
    assert suggest(Fake(VEC), [], ["q_code"], CFG) == [Suggestion(None, 0.0, None)]


def test_config_defaults():
    c = load_embed_config()
    assert (c.model, c.k, c.min_score, c.min_margin, c.max_refs_per_category) == (
        "mlx-community/all-MiniLM-L6-v2-bf16", 5, 0.40, 0.10, 200)


def test_unknown_label_rejected():
    with pytest.raises(ValueError):
        suggest(Fake(VEC), [("code1", "Music")], ["q_code"], CFG)
