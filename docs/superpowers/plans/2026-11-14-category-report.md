# Category Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only report of uncategorized/unsure time with strict nearest-neighbour category suggestions.

**Architecture:** `labels/embed.py` (embedder + pure-MLX kNN `suggest`), `labels/report.py` (aggregation + CLI), config in `labels/embed.yaml`.

**Tech Stack:** Python 3.13 (uv), MLX, mlx-embeddings (uv group `embed`), pytest.

**Spec:** `docs/superpowers/specs/2026-11-14-category-report-design.md`

## Global Constraints
- Read-only: `data.db.open_existing()`; never write files, the DB or a key. Output to stdout only.
- Suggestions only from {Coding, Docs, Chat, Video, Social, neutral}; null when below `min_score` 0.40 or margin < `min_margin` 0.10.
- MLX on the CPU stream via `common/device.py`; no `mx.gpu`/`mx.cpu` literals.
- `mlx-embeddings` lives in uv group `embed`; the report must work (no suggestions) without it.

## Review Focus
- Excluded samples (no title/domain) never appear in any table or reference set. Test in Task 2.
- Empty period (no samples) prints a clear "no samples" line, exit 0. Test in Task 2.
- A category with no reference examples can never be suggested. Test in Task 1.
- Ties/near-ties between two categories give null (strict). Test in Task 1.
- `--no-titles` removes titles from both table and JSON. Test in Task 2.

---

### Task 1: Strict kNN suggestions

**Files:** Create `labels/embed.py`, `labels/embed.yaml`; Test `tests/labels/test_embed.py`; modify `pyproject.toml`/`uv.lock` via `uv add --group embed mlx-embeddings`.

**Interfaces:**
- Produces: `EmbedConfig(model: str, k: int, min_score: float, min_margin: float, max_refs_per_category: int)`, `load_embed_config(path=None) -> EmbedConfig`; `class Embedder(Protocol): def embed(self, texts: list[str]) -> mx.array`; `Suggestion(category: str | None, score: float, runner_up: str | None)`; `suggest(embedder, refs: list[tuple[str, str]], queries: list[str], cfg) -> list[Suggestion]` (refs are (text, label)); `load_minilm(cfg) -> Embedder` (raises `ImportError` with the `uv sync --group embed` hint).

- [ ] Step 1: tests with a fake embedder mapping texts to fixed unit vectors: `test_clear_match_suggested` (score ≥ 0.4, margin ≥ 0.1 → label), `test_low_score_is_none`, `test_near_tie_is_none`, `test_unreferenced_category_never_suggested`, `test_no_refs_all_none`, `test_config_defaults` (values above).
- [ ] Step 2: run → FAIL (import).
- [ ] Step 3: implement. Score per category = mean similarity of that category's members within the top-k neighbours (cosine; vectors normalised); best vs runner-up for margin.
- [ ] Step 4: `uv run pytest tests/labels/test_embed.py -v` → PASS.
- [ ] Step 5: commit `feat(labels): strict nearest-neighbour category suggestions`.

### Task 2: Report aggregation and CLI

**Files:** Create `labels/report.py`; Test `tests/labels/test_report.py`.

**Interfaces:**
- Consumes: Task 1 `suggest`, `load_minilm`, `load_embed_config`.
- Produces: `KeyStat(key: str, seconds: float, title: str | None, category: str, low_confidence: bool)`; `Report(total_s: float, by_category: dict[str, float], unknown: list[KeyStat], unsure: list[KeyStat], refs: list[tuple[str, str]])`; `build_report(samples: Iterable[dict], rules) -> Report` (pure); `main(argv) -> int`.

- [ ] Step 1: tests on in-memory sample dicts: `test_time_by_category_with_neutral_split` ("neutral" for Other/high confidence), `test_unknown_and_unsure_ranked_by_time_with_top_title`, `test_excluded_never_listed`, `test_refs_are_high_confidence_categorized_only`, `test_empty_report_message` (main with no samples prints "no samples", exit 0, via an injectable connection), `test_json_no_titles` (`--json --no-titles --no-suggest`: valid JSON lines, no `title` values).
- [ ] Step 2: run → FAIL.
- [ ] Step 3: implement. SQL: samples with `ts >= now - days*86400`. Ref text = `f"{key} {title}"`; refs capped per category by time.
- [ ] Step 4: `uv run pytest -q` → PASS; then a real run `uv run --group embed python -m labels.report --days 7` reads the user's DB (manual check).
- [ ] Step 5: commit `feat(labels): category report with strict suggestions`.
