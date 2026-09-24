"""Local cross-encoder reranker — the coherence-rerank stage of Library hybrid
search.

Lazily loads a sentence-transformers ``CrossEncoder`` (the model downloads once
on first use) and scores ``(query, document)`` pairs. The load runs in a
background worker so search can use fusion while the model downloads. A failed
load is not a pending load: the next readiness check raises its original error.
Inference errors also propagate. A process-level singleton keeps the model resident.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor

from sentence_transformers import CrossEncoder

LOGGER = logging.getLogger("zotero_summarizer.reranker")


class Reranker:
    """Lazy, thread-safe wrapper around a CrossEncoder relevance reranker."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._model = None
        self._predict_lock = threading.Lock()  # torch inference is not thread-safe
        self._load_lock = threading.Lock()      # one load at a time
        self._load_future: Future[None] | None = None

    def is_ready(self) -> bool:
        if self._load_future is not None and self._load_future.done():
            self._load_future.result()
        return self._model is not None

    def is_loading(self) -> bool:
        return self._load_future is not None and not self._load_future.done()

    def _load(self) -> None:
        if self._model is not None:
            return
        LOGGER.info("Loading cross-encoder reranker: %s (downloads once)", self.model_name)
        self._model = CrossEncoder(self.model_name, max_length=512)
        LOGGER.info("Reranker ready: %s", self.model_name)

    def ensure_loaded_async(self) -> None:
        """Start a background load if not loaded/loading — non-blocking, so the
        first search returns fusion results immediately while the model downloads;
        the next search reranks."""
        if self.is_ready():
            return
        with self._load_lock:
            if self._load_future is not None or self.is_ready():
                return
            # ponytail: one load/model; use a process if bounded shutdown is needed.
            pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="reranker-load")
            try:
                self._load_future = pool.submit(self._load)
            finally:
                pool.shutdown(wait=False)

    def rerank(self, query: str, pairs: list[tuple[str, str]], top_n: int) -> list[tuple[str, float]]:
        """``[(item_key, score)]`` sorted by descending relevance, capped to
        ``top_n``. ``pairs`` = ``(item_key, document_text)``. Returns ``[]`` when
        the model is unstarted/loading or there are no pairs. Load/inference
        failures raise; callers must not turn them into fusion results."""
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
