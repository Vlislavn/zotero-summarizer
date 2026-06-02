"""Local cross-encoder reranker — the coherence-rerank stage of Library hybrid
search.

Lazily loads a sentence-transformers ``CrossEncoder`` (the model downloads once
on first use) and scores ``(query, document)`` pairs. The load runs in a
BACKGROUND thread so the first search never blocks on a large download — until
the model is ready the caller uses the BM25+dense fusion order (degradation
ladder requested for "search must work while the local model downloads"). A
process-level singleton keeps the model resident.
"""
from __future__ import annotations

import logging
import threading

try:
    from sentence_transformers import CrossEncoder
except Exception:  # pragma: no cover - optional dependency boundary
    CrossEncoder = None

LOGGER = logging.getLogger("zotero_summarizer.reranker")


class Reranker:
    """Lazy, thread-safe wrapper around a CrossEncoder relevance reranker."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._model = None
        self._predict_lock = threading.Lock()  # torch inference is not thread-safe
        self._load_lock = threading.Lock()      # one load at a time
        self._loading = False
        self._load_failed = False

    def is_ready(self) -> bool:
        return self._model is not None

    def is_loading(self) -> bool:
        return self._loading

    def _load(self) -> None:
        if CrossEncoder is None:
            LOGGER.warning("sentence-transformers CrossEncoder unavailable; rerank off (fusion only)")
            self._load_failed = True
            return
        if self._model is not None or self._load_failed:
            return
        LOGGER.info("Loading cross-encoder reranker: %s (downloads once)", self.model_name)
        try:
            self._model = CrossEncoder(self.model_name, max_length=512)
            LOGGER.info("Reranker ready: %s", self.model_name)
        except Exception:
            # Boundary: model download/load failure must degrade to fusion order,
            # not break search (requested). Logged loudly; never retried-in-loop.
            LOGGER.exception("Failed to load reranker %s; using fusion order", self.model_name)
            self._load_failed = True

    def ensure_loaded_async(self) -> None:
        """Start a background load if not loaded/loading — non-blocking, so the
        first search returns fusion results immediately while the model downloads;
        the next search reranks."""
        if self._model is not None or self._load_failed or CrossEncoder is None:
            return
        with self._load_lock:
            if self._loading or self._model is not None or self._load_failed:
                return
            self._loading = True

        def _worker() -> None:
            try:
                self._load()
            finally:
                self._loading = False

        threading.Thread(target=_worker, name="reranker-load", daemon=True).start()

    def rerank(self, query: str, pairs: list[tuple[str, str]], top_n: int) -> list[tuple[str, float]]:
        """``[(item_key, score)]`` sorted by descending relevance, capped to
        ``top_n``. ``pairs`` = ``(item_key, document_text)``. Returns ``[]`` when
        the model isn't ready (the caller then keeps the fusion order)."""
        if not self.is_ready() or not pairs:
            return []
        model = self._model
        inputs = [(query, text) for _, text in pairs]
        with self._predict_lock:  # torch inference is not thread-safe
            scores = model.predict(inputs)
        ranked = sorted(
            ((pairs[i][0], float(scores[i])) for i in range(len(pairs))),
            key=lambda kv: kv[1],
            reverse=True,
        )
        return ranked[:top_n]


_INSTANCES: dict[str, Reranker] = {}
_INSTANCES_LOCK = threading.Lock()


def get_reranker(model_name: str) -> Reranker:
    """Process-level singleton per model name (keeps the loaded model resident)."""
    with _INSTANCES_LOCK:
        inst = _INSTANCES.get(model_name)
        if inst is None:
            inst = Reranker(model_name)
            _INSTANCES[model_name] = inst
        return inst
