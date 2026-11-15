# Category report with strict embedding suggestions (short design)

## Intent
Show where logged time is still uncategorized ("Other", low confidence) or unsure, and for each unknown
app/site suggest one of the categories we already have. This decides whether more rules are enough
or a live embedding fallback is worth building. User asked for a "strict" model that only resorts to
existing categories, with structured (JSON) output, suggestions reviewed by the user before any rule changes.

## Goals / non-goals / anti-goals
- Goal: `uv run python -m labels.report [--days 7] [--top 15] [--json] [--no-titles] [--no-suggest]`.
- Goal: time per category; top unknown keys (domain or bundle id) and top unsure-but-known keys by minutes,
  with the most common title (truncated to 50 chars).
- Goal: per unknown key, `suggested` in {Coding, Docs, Chat, Video, Social, neutral} or null, plus a score.
- Non-goal: changing categorization, windows or the live path. Non-goal: auto-writing categories.yaml.
- Non-goal: categorizer-vs-check-in eval (check-ins are states, not categories; `labels.tune` covers agreement).
- Anti-goal: anything leaving the Mac besides the one-time weight download; titles/embeddings on disk.

## Approaches
- **A (picked): nearest neighbours over a small sentence-embedding model (MLX).** Embed "key title" for
  every rule-labelled (high-confidence) key/title seen in the period, and each unknown key; similarity-weighted
  vote over the top k. Strict: no suggestion unless the best category clears a score threshold and beats the
  runner-up by a margin. Fast, gives a real confidence, improves as rules label more text.
- B: small local LLM picking a category. Rejected for now: far bigger/slower and harder to keep strict.
- C: category descriptions only (zero-shot). Kept as a fallback idea, not built (YAGNI).

## Design
- `labels/report.py`: read-only (`data.db.open_existing`), samples in the last N days; per sample
  `categorize()`; aggregates seconds per category, per unknown key, per low-confidence known key.
  Neutral (Other, high confidence) shows as its own line.
- `labels/embed.py`: `Embedder` protocol `embed(texts) -> mx.array (n, d)` (L2-normalised); `MiniLMEmbedder`
  wraps `mlx_embeddings` on the CPU stream (via `common/device.py`); `suggest(refs, queries, cfg)` is pure
  MLX math and testable with a fake embedder.
- Config `labels/embed.yaml`: model `mlx-community/all-MiniLM-L6-v2-bf16`, `k: 5`, `min_score: 0.40`,
  `min_margin: 0.10`, `max_refs_per_category: 200` (most time first, deterministic).
- Dependency: `mlx-embeddings` in a uv dependency group `embed` (it pulls ~67 packages, so the default env and
  the deployed jobs stay lean). Without it the report runs and says suggestions need `uv sync --group embed`.
- Privacy: titles and vectors stay in memory, output to stdout only. `HF_HUB_DISABLE_TELEMETRY=1`; once the
  model is cached, load with offline mode.
- Spike (2026-11-14): "some-blog.dev Rust borrow checker explained" -> Coding 0.44 (runner-up 0.22);
  news/rumour/Finder all < 0.35 -> no suggestion under these thresholds. ~10 ms per 4 texts on CPU.

## Left to the builder
Exact table formatting; test cases.
