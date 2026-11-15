"""Strict nearest-neighbour category suggestions over a small sentence-embedding model (MLX).

Reference texts are app/site keys + titles the rules already categorized (high confidence). A query
gets a category only if that category's score (mean similarity of its members among the top-k
neighbours) clears ``min_score`` and beats the runner-up by ``min_margin``; otherwise None. Only
categories that have references can be suggested, so it never invents one.

Privacy: texts and vectors stay in memory. The only network use is the one-time weight download.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, Protocol, Sequence

import mlx.core as mx
import yaml

from common.device import train_stream
from labels.categorizer import CATEGORIES

DEFAULT_PATH = Path(__file__).parent / "embed.yaml"
LABELS = (*CATEGORIES, "neutral")
INSTALL_HINT = "category suggestions need the embed group: uv sync --group embed"


@dataclass(frozen=True)
class EmbedConfig:
    model: str
    k: int
    min_score: float
    min_margin: float
    max_refs_per_category: int


def load_embed_config(path: Path | None = None) -> EmbedConfig:
    d = yaml.safe_load((path or DEFAULT_PATH).read_text()) or {}
    return EmbedConfig(model=str(d["model"]), k=int(d["k"]), min_score=float(d["min_score"]),
                       min_margin=float(d["min_margin"]), max_refs_per_category=int(d["max_refs_per_category"]))


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> mx.array: ...  # (n, d), L2-normalised rows


class Suggestion(NamedTuple):
    category: str | None
    score: float
    runner_up: str | None


def suggest(embedder: Embedder, refs: Sequence[tuple[str, str]], queries: Sequence[str],
            cfg: EmbedConfig) -> list[Suggestion]:
    for _, label in refs:
        if label not in LABELS:
            raise ValueError(f"unknown reference label {label!r}")
    if not refs or not queries:
        return [Suggestion(None, 0.0, None) for _ in queries]
    labels = [lab for _, lab in refs]
    with train_stream():
        R = embedder.embed([t for t, _ in refs])
        Q = embedder.embed(list(queries))
        sims = Q @ R.T
        mx.eval(sims)
    out = []
    k = min(cfg.k, len(refs))
    for row in sims.tolist():
        top = sorted(range(len(row)), key=lambda i: -row[i])[:k]
        by_cat: dict[str, list[float]] = {}
        for i in top:
            by_cat.setdefault(labels[i], []).append(row[i])
        ranked = sorted(((sum(v) / len(v), c) for c, v in by_cat.items()), reverse=True)
        best_s, best_c = ranked[0]
        second_s, second_c = ranked[1] if len(ranked) > 1 else (0.0, None)
        ok = best_s >= cfg.min_score and best_s - second_s >= cfg.min_margin
        out.append(Suggestion(best_c if ok else None, round(best_s, 3), second_c))
    return out


class MiniLMEmbedder:
    """mlx-embeddings wrapper (mean-pooled, normalised sentence embeddings)."""

    def __init__(self, model, tokenizer):
        self.model, self.tokenizer = model, tokenizer

    def embed(self, texts: list[str]) -> mx.array:
        tok = self.tokenizer._tokenizer(texts, padding=True, truncation=True, max_length=128, return_tensors="np")
        out = self.model(**{k: mx.array(v) for k, v in tok.items()})
        return out.text_embeds


def load_minilm(cfg: EmbedConfig) -> MiniLMEmbedder:
    """Load the model, from the local cache when present (offline), else download once."""
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    try:
        from huggingface_hub import snapshot_download
        from mlx_embeddings.utils import load
    except ImportError as e:
        raise ImportError(INSTALL_HINT) from e
    try:
        path = snapshot_download(cfg.model, local_files_only=True)
    except Exception:  # not cached yet: the one allowed network use
        path = snapshot_download(cfg.model)
    model, tokenizer = load(path)
    return MiniLMEmbedder(model, tokenizer)
