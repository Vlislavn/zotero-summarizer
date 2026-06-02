"""Guarded local code-embedding backend for the on-demand overlap audit.

This is the ONLY module in the overlap tool that imports the heavy
``sentence-transformers`` stack, and it does so **lazily inside ``get_embedder``**
so ``check_overlaps.py`` (and its tests) import torch-free.

GRACEFUL DEGRADATION IS EXPLICITLY REQUESTED by the approved overlap-audit plan:
"Degrades gracefully to deterministic-only (struct+api) when the model is
absent/offline-uncached — this is the user's explicit, announced fallback (printed
reason to stderr), not a silent swallow." Accordingly ``get_embedder`` returns
``None`` (never raises) when the backend or model is unavailable, **after printing
the concrete reason to stderr**; the caller (`check_overlaps`) checks for ``None``
and scores with the deterministic signals only. This is a boundary to an external
model hub, which is exactly where an announced fallback belongs.
"""
from __future__ import annotations

import os
import sys
from typing import Callable

# Code-specialised default (sentence-transformers-loadable; needs trust_remote_code).
# Override with ``--model`` — e.g. ``sentence-transformers/all-MiniLM-L6-v2`` for a
# zero-download, already-prefetched, fully-offline run.
DEFAULT_MODEL = "jinaai/jina-embeddings-v2-base-code"
_MAX_LENGTH = 512
_BATCH_SIZE = 32

# model_id -> a built embedder closure on success, or None once a load was tried and
# announced as failed (so the reason is printed once, not per call).
_TRIED: dict[str, "Callable[[list[str]], object] | None"] = {}


def _apply_offline_env() -> None:
    """Mirror the app's ``ZS_OFFLINE`` -> HF offline env (cache-only HF loads)."""
    val = (os.getenv("ZS_OFFLINE") or "").strip().lower()
    if val in ("1", "true", "yes", "on") or (os.getenv("HF_HUB_OFFLINE") or "").strip() == "1":
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"


def _make_closure(model: object) -> "Callable[[list[str]], object]":
    """Return ``embed(texts) -> np.ndarray`` of L2-normalized rows for ``model``."""

    def embed(texts: list[str]) -> object:
        import numpy as np

        vectors = model.encode(  # type: ignore[attr-defined]
            list(texts),
            batch_size=_BATCH_SIZE,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return np.asarray(vectors, dtype="float32")

    return embed


def get_embedder(model_id: str) -> "Callable[[list[str]], object] | None":
    """Return a batched L2-normalizing embedder for ``model_id``, or ``None``.

    Returns ``None`` (with a one-line stderr reason) when ``sentence-transformers``
    is unimportable or the model cannot be loaded (e.g. offline + uncached) — the
    user-authorized graceful-degradation path documented at the module top.
    """
    if model_id in _TRIED:
        return _TRIED[model_id]
    _apply_offline_env()
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        sys.stderr.write(f"  (embedding model unavailable: sentence-transformers not importable — {exc})\n")
        _TRIED[model_id] = None
        return None
    # Loading a (possibly trust_remote_code) model from the HF hub/cache is the
    # external boundary; the user's plan authorizes announcing any load failure and
    # degrading rather than aborting the advisory audit.
    try:
        model = SentenceTransformer(model_id, trust_remote_code=True)
        model.max_seq_length = _MAX_LENGTH  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 — announced, user-authorized degradation (see module docstring)
        sys.stderr.write(f"  (embedding model unavailable: could not load '{model_id}' — {type(exc).__name__}: {exc})\n")
        _TRIED[model_id] = None
        return None
    embedder = _make_closure(model)
    _TRIED[model_id] = embedder
    return embedder
