"""Classifier embed functions (split from classifier.py)."""
from __future__ import annotations

import hashlib  # noqa: F401
import json  # noqa: F401
import logging  # noqa: F401
import sqlite3  # noqa: F401
import threading
import time  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any, Callable  # noqa: F401

import numpy as np  # noqa: F401

from zotero_summarizer.services.model.classifier_const import *  # noqa: F401,F403

# Process-wide cache of the loaded SPECTER2 model/tokenizer (lazy, first call).
_MODEL_CACHE: dict[str, Any] = {}
# transformers' meta-device weight init (accelerate `init_empty_weights`) patches
# GLOBAL torch state and is NOT thread-safe; two concurrent first-loads interleave
# and leave one model's params on the `meta` device → `mdl.to(device)` raises
# "Cannot copy out of meta tensor". Serialize the load to one thread at a time, and
# the forward pass too (torch inference is not thread-safe, and `_encode_chunk`
# mutates the shared device on its MPS→CPU fallback). Mirrors reranker.py /
# claim_checker.py, which already guard their models the same way.
_LOAD_LOCK = threading.Lock()
_PREDICT_LOCK = threading.Lock()


def _content_hash(title: str, abstract: str, authors: str = "", venue: str = "") -> str:
    """Stable identity for SPECTER2 embedding cache.

    Sprint-3a (May 2026): the hash mixes `title|abstract|adapter-name` so
    that swapping the proximity adapter automatically invalidates every
    cached vector. `authors` and `venue` are accepted for backward compat
    but no longer affect the hash (Sprint 1).
    """
    blob = f"{title}|||{abstract}|||{SPECTER2_ADAPTER_NAME}"
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _select_device(torch: Any) -> str:
    """Pick the fastest available device for encoder inference: Apple-Silicon
    MPS, then CUDA, then CPU. A small (~0.5GB) encoder runs comfortably on the
    GPU and is far faster there than on CPU for the per-tick gate batch."""
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _load_specter2() -> tuple[Any, Any, Any]:
    """Lazy-load SPECTER2 base + proximity adapter. Returns (tokenizer, model, torch).

    Sprint-3a: switched from `transformers.AutoModel` to
    `adapters.AutoAdapterModel` so we can load the proximity adapter on
    top of the base encoder. The adapter is set active so subsequent
    forward passes route through it.

    The model is moved onto the selected device (MPS/CUDA/CPU); the device is
    cached so the encode helpers know where to place inputs.
    """
    if "loaded" in _MODEL_CACHE:                       # fast path: no lock once warm
        return _MODEL_CACHE["tok"], _MODEL_CACHE["mdl"], _MODEL_CACHE["torch"]
    with _LOAD_LOCK:                                   # one materialization at a time
        if "loaded" in _MODEL_CACHE:                   # re-check: a peer loaded while we waited
            return _MODEL_CACHE["tok"], _MODEL_CACHE["mdl"], _MODEL_CACHE["torch"]
        LOGGER.info(
            "loading SPECTER2 base %r + proximity adapter %r (first call ~400MB+50MB)",
            SPECTER2_MODEL_NAME, SPECTER2_ADAPTER_NAME,
        )
        import torch
        from adapters import AutoAdapterModel
        from transformers import AutoTokenizer

        try:
            tok = AutoTokenizer.from_pretrained(SPECTER2_MODEL_NAME)
            mdl = AutoAdapterModel.from_pretrained(SPECTER2_MODEL_NAME)
            mdl.load_adapter(
                SPECTER2_ADAPTER_NAME,
                source="hf",
                load_as="proximity",
                set_active=True,
            )
        except Exception as exc:
            # The gate has no local fallback — scoring needs SPECTER2. Turn an opaque
            # HuggingFace/offline error into an actionable one (re-raised, not swallowed):
            # offline + not-yet-cached is the common cause.
            raise RuntimeError(
                f"Could not load the SPECTER2 gate encoder "
                f"({SPECTER2_MODEL_NAME} + {SPECTER2_ADAPTER_NAME}). If you are offline, "
                f"the model is not cached yet — run `zotero-summarizer prefetch-models` "
                f"while online once to populate the cache. Original error: {exc}"
            ) from exc
        mdl.eval()
        device = _select_device(torch)
        mdl.to(device)
        _MODEL_CACHE.update({"tok": tok, "mdl": mdl, "torch": torch, "device": device, "loaded": True})
        LOGGER.info("SPECTER2 + proximity adapter ready on device=%s", device)
        return tok, mdl, torch


def _pair_to_text(tok: Any, title: str, abstract: str) -> str:
    """``title [SEP] abstract`` — the layout SPECTER2 was trained on (Cohan 2020).
    Authors/venue are deliberately excluded (they pushed the first tokens
    off-distribution); that signal lives in the tabular library features."""
    parts = [p for p in [(title or "Untitled").strip(), (abstract or "").strip()] if p]
    return tok.sep_token.join(parts)


def _encode_chunk(tok: Any, mdl: Any, torch: Any, texts: list[str], device: str) -> np.ndarray:
    """One batched forward pass → (len(texts), 768) CLS vectors on CPU.

    Device→CPU fallback (user-authorized: "Batch + MPS GPU, CPU fallback"):
    MPS op coverage is incomplete, so a device RuntimeError/NotImplementedError
    is logged and the batch is retried on CPU, after which the model stays on CPU
    for the rest of the process. An error on CPU is real and propagates.
    """
    inputs = tok(texts, padding=True, truncation=True, max_length=512, return_tensors="pt")
    # Serialize the forward pass: torch inference is not thread-safe, and the
    # fallback below moves the shared model + mutates the shared device, which a
    # concurrent encode must not observe mid-flight.
    with _PREDICT_LOCK:
        try:
            moved = {k: v.to(device) for k, v in inputs.items()}
            with torch.no_grad():
                outputs = mdl(**moved)
            return outputs.last_hidden_state[:, 0, :].cpu().numpy().astype(np.float32)
        except (RuntimeError, NotImplementedError) as exc:
            if device == "cpu":
                raise
            LOGGER.warning("SPECTER2 forward failed on device=%s (%s); falling back to cpu", device, exc)
            mdl.to("cpu")
            _MODEL_CACHE["device"] = "cpu"
            with torch.no_grad():
                outputs = mdl(**{k: v.to("cpu") for k, v in inputs.items()})
            return outputs.last_hidden_state[:, 0, :].cpu().numpy().astype(np.float32)


def compute_embeddings_batch(
    pairs: list[tuple[str, str]],
    *,
    sub_batch: int = 32,
) -> np.ndarray:
    """Embed many ``(title, abstract)`` pairs in batched forward passes.

    Returns ``(N, 768)`` float32. This is the throughput primitive for the gate:
    one forward pass over a sub-batch instead of N single-item passes. Inputs run
    on the selected device (MPS/CUDA/CPU) with the CPU fallback in ``_encode_chunk``.
    """
    if not pairs:
        return np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
    tok, mdl, torch = _load_specter2()
    device = _MODEL_CACHE["device"]
    texts = [_pair_to_text(tok, title, abstract) for title, abstract in pairs]
    out = np.zeros((len(texts), EMBEDDING_DIM), dtype=np.float32)
    for start in range(0, len(texts), sub_batch):
        chunk = texts[start:start + sub_batch]
        out[start:start + len(chunk)] = _encode_chunk(tok, mdl, torch, chunk, device)
    return out


def compute_embedding(
    title: str,
    abstract: str,
    *,
    authors: str = "",
    venue: str = "",
) -> np.ndarray:
    """Run SPECTER2 on one item. Returns a (768,) float32 ndarray.

    Thin shim over :func:`compute_embeddings_batch` (kept for back-compat). The
    ``authors``/``venue`` kwargs are accepted but not mixed into the text or the
    cache hash (see :func:`_pair_to_text`).
    """
    return compute_embeddings_batch([(title, abstract)])[0]


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


def get_or_compute_embeddings_batch(
    db_path: Path,
    items: list[dict[str, str]],
) -> np.ndarray:
    """Cache-aware batch embed. ``items`` need ``item_key``/``title``/``abstract``.

    Returns ``(N, 768)`` in input order. Cache hits (matching ``content_hash``)
    are reused; the misses are encoded in ONE batched pass and inserted. This is
    the gate's hot-path entry point — a fresh backlog of N items costs one
    batched forward instead of N single passes.
    """
    _ensure_schema(db_path)
    n = len(items)
    out = np.zeros((n, EMBEDDING_DIM), dtype=np.float32)
    if n == 0:
        return out
    keys = [str(it.get("item_key") or it.get("item_id") or f"item_{i}") for i, it in enumerate(items)]
    hashes = [_content_hash(it.get("title") or "", it.get("abstract") or "") for it in items]
    conn = sqlite3.connect(str(db_path))
    try:
        miss_idx: list[int] = []
        for i, (k, ch) in enumerate(zip(keys, hashes)):
            row = conn.execute(
                "SELECT content_hash, embedding_json FROM specter2_embeddings WHERE item_key = ?",
                (k,),
            ).fetchone()
            if row and row[0] == ch:
                out[i] = np.asarray(json.loads(row[1]), dtype=np.float32)
            else:
                miss_idx.append(i)
        if miss_idx:
            pairs = [(items[i].get("title") or "", items[i].get("abstract") or "") for i in miss_idx]
            embs = compute_embeddings_batch(pairs)
            for j, i in enumerate(miss_idx):
                out[i] = embs[j]
                conn.execute(
                    "INSERT OR REPLACE INTO specter2_embeddings (item_key, content_hash, embedding_json) "
                    "VALUES (?, ?, ?)",
                    (keys[i], hashes[i], json.dumps(embs[j].tolist())),
                )
            conn.commit()
        return out
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
    "_select_device",
    "_load_specter2",
    "compute_embedding",
    "compute_embeddings_batch",
    "get_or_compute_embedding",
    "get_or_compute_embeddings_batch",
    "_ensure_schema",
    "_embedding_cached",
]
