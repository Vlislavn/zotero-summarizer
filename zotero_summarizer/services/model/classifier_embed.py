"""Classifier embed functions (split from classifier.py)."""
from __future__ import annotations

import hashlib  # noqa: F401
import json  # noqa: F401
import logging  # noqa: F401
import sqlite3  # noqa: F401
import time  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any, Callable  # noqa: F401

import numpy as np  # noqa: F401

from zotero_summarizer.services.model.classifier_const import *  # noqa: F401,F403

# Process-wide cache of the loaded SPECTER2 model/tokenizer (lazy, first call).
_MODEL_CACHE: dict[str, Any] = {}


def _content_hash(title: str, abstract: str, authors: str = "", venue: str = "") -> str:
    """Stable identity for SPECTER2 embedding cache.

    Sprint-3a (May 2026): the hash mixes `title|abstract|adapter-name` so
    that swapping the proximity adapter automatically invalidates every
    cached vector. `authors` and `venue` are accepted for backward compat
    but no longer affect the hash (Sprint 1).
    """
    blob = f"{title}|||{abstract}|||{SPECTER2_ADAPTER_NAME}"
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _load_specter2() -> tuple[Any, Any, Any]:
    """Lazy-load SPECTER2 base + proximity adapter. Returns (tokenizer, model, torch).

    Sprint-3a: switched from `transformers.AutoModel` to
    `adapters.AutoAdapterModel` so we can load the proximity adapter on
    top of the base encoder. The adapter is set active so subsequent
    forward passes route through it.
    """
    if "loaded" in _MODEL_CACHE:
        return _MODEL_CACHE["tok"], _MODEL_CACHE["mdl"], _MODEL_CACHE["torch"]
    LOGGER.info(
        "loading SPECTER2 base %r + proximity adapter %r (first call ~400MB+50MB)",
        SPECTER2_MODEL_NAME, SPECTER2_ADAPTER_NAME,
    )
    import torch
    from adapters import AutoAdapterModel
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(SPECTER2_MODEL_NAME)
    mdl = AutoAdapterModel.from_pretrained(SPECTER2_MODEL_NAME)
    mdl.load_adapter(
        SPECTER2_ADAPTER_NAME,
        source="hf",
        load_as="proximity",
        set_active=True,
    )
    mdl.eval()
    _MODEL_CACHE.update({"tok": tok, "mdl": mdl, "torch": torch, "loaded": True})
    LOGGER.info("SPECTER2 + proximity adapter ready")
    return tok, mdl, torch


def compute_embedding(
    title: str,
    abstract: str,
    *,
    authors: str = "",
    venue: str = "",
) -> np.ndarray:
    """Run SPECTER2 once. Returns a (768,) float32 ndarray.

    Sprint-1 (May 2026): input layout is ``title [SEP] abstract`` — the
    layout SPECTER2 was actually trained on (Cohan 2020). Authors and venue
    used to be concatenated into the text but they pushed the encoder's
    first 30 tokens off-distribution and let surname collisions (Wang/Li/
    Chen) spuriously inflate cosine similarity. Author/venue signal is
    captured by tabular library-conditioned features instead.

    The ``authors`` and ``venue`` kwargs are accepted for backward
    compatibility but are no longer mixed into the text or the cache hash.
    """
    tok, mdl, torch = _load_specter2()
    parts = [p for p in [
        (title or "Untitled").strip(),
        (abstract or "").strip(),
    ] if p]
    text = tok.sep_token.join(parts)
    inputs = tok(text, padding=True, truncation=True, max_length=512, return_tensors="pt")
    with torch.no_grad():
        outputs = mdl(**inputs)
    cls = outputs.last_hidden_state[:, 0, :].squeeze(0).cpu().numpy()
    return cls.astype(np.float32)


def get_or_compute_embedding(
    db_path: Path,
    item_key: str,
    title: str,
    abstract: str,
    *,
    authors: str = "",
    venue: str = "",
) -> np.ndarray:
    """Return cached embedding when content_hash matches, otherwise compute."""
    _ensure_schema(db_path)
    ch = _content_hash(title, abstract, authors=authors, venue=venue)
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT content_hash, embedding_json FROM specter2_embeddings WHERE item_key = ?",
            (item_key,),
        ).fetchone()
        if row and row[0] == ch:
            return np.asarray(json.loads(row[1]), dtype=np.float32)
        emb = compute_embedding(title, abstract, authors=authors, venue=venue)
        conn.execute(
            "INSERT OR REPLACE INTO specter2_embeddings (item_key, content_hash, embedding_json) "
            "VALUES (?, ?, ?)",
            (item_key, ch, json.dumps(emb.tolist())),
        )
        conn.commit()
        return emb
    finally:
        conn.close()


def _ensure_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _embedding_cached(db_path: Path, item_key: str, content_hash: str) -> bool:
    if not db_path.exists():
        return False
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT 1 FROM specter2_embeddings WHERE item_key=? AND content_hash=?",
            (item_key, content_hash),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()
    return row is not None


__all__ = [
    "_content_hash",
    "_load_specter2",
    "compute_embedding",
    "get_or_compute_embedding",
    "_ensure_schema",
    "_embedding_cached",
]
