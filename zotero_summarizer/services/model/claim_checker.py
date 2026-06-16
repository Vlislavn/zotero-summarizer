"""Local MiniCheck claim-support scorer — the deterministic ENCODER alternative
to the LLM overstatement judge (Phase A: SHADOW only, no behavior change).

Wraps MiniCheck (Tang et al. 2024, https://github.com/Liyan06/MiniCheck): a
Flan-T5 / DeBERTa / RoBERTa encoder fine-tuned to score whether a claim is
supported by a document — reference-free, deterministic (same input → same
score), and ~445× cheaper than an LLM judge. Mirrors ``reranker.py``: a lazy
process-level singleton, an optional-dependency boundary (a missing ``minicheck``
package or a load failure degrades to ``is_ready()=False`` — the caller logs and
keeps the LLM verdict), and standard HF-cache loading (offline-aware via
``ZS_OFFLINE``/``HF_HUB_OFFLINE``, same cache the bge models use).

Enable the optional dependency to turn the shadow on:
    uv pip install "minicheck @ git+https://github.com/Liyan06/MiniCheck.git@main"
"""
from __future__ import annotations

import logging
import threading

LOGGER = logging.getLogger("zotero_summarizer.claim_checker")

# MiniCheck variant -> the HF repo it downloads (for prefetch / offline reporting).
MINICHECK_REPOS: dict[str, str] = {
    "flan-t5-large": "lytang/MiniCheck-Flan-T5-Large",
    "deberta-v3-large": "lytang/MiniCheck-DeBERTa-v3-Large",
    "roberta-large": "lytang/MiniCheck-RoBERTa-Large",
}
DEFAULT_MODEL = "flan-t5-large"


def hf_repo_for(model_name: str) -> str:
    """The Hugging Face repo id a MiniCheck variant downloads (for prefetch)."""
    return MINICHECK_REPOS.get(model_name, MINICHECK_REPOS[DEFAULT_MODEL])


class ClaimChecker:
    """Lazy, thread-safe MiniCheck encoder scoring claim⊨evidence support probs."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._scorer = None
        self._predict_lock = threading.Lock()  # torch inference is not thread-safe
        self._load_lock = threading.Lock()
        self._load_failed = False

    def is_ready(self) -> bool:
        return self._scorer is not None

    def _load(self) -> None:
        with self._load_lock:
            if self._scorer is not None or self._load_failed:
                return
            try:
                from minicheck.minicheck import MiniCheck
            except Exception:  # optional-dependency boundary (mirrors reranker.py)
                LOGGER.warning(
                    "minicheck not installed; encoder claim-check off. Enable with: "
                    "uv pip install 'minicheck @ git+https://github.com/Liyan06/MiniCheck.git@main'"
                )
                self._load_failed = True
                return
            LOGGER.info("Loading MiniCheck claim-checker: %s (downloads once)", self.model_name)
            try:
                # cache_dir=None → standard HF cache (where the bge models live;
                # honors HF_HUB_OFFLINE/ZS_OFFLINE). Avoids MiniCheck's cwd-relative
                # './ckpts' default, which would violate the data/-only state rule.
                self._scorer = MiniCheck(model_name=self.model_name, cache_dir=None)
                LOGGER.info("MiniCheck ready: %s", self.model_name)
            except Exception:  # model load/download boundary (mirrors reranker.py)
                LOGGER.exception("Failed to load MiniCheck %s; encoder claim-check off", self.model_name)
                self._load_failed = True

    def score(self, claims: list[str], evidences: list[str]) -> list[float] | None:
        """Per-claim max support probability in [0, 1], aligned to ``claims``.
        ``evidences[i]`` is the document text retrieved for ``claims[i]``. Returns
        ``None`` when the encoder is unavailable (the caller keeps the LLM verdict)."""
        if not claims or len(claims) != len(evidences):
            return None
        if self._scorer is None and not self._load_failed:
            self._load()
        if self._scorer is None:
            return None
        try:
            with self._predict_lock:
                _labels, probs, _, _ = self._scorer.score(docs=list(evidences), claims=list(claims))
        except Exception:  # inference boundary: a shadow scorer must never break the eval
            LOGGER.exception("MiniCheck scoring failed; skipping the encoder shadow this run")
            return None
        return [float(p) for p in probs]


_INSTANCES: dict[str, ClaimChecker] = {}
_INSTANCES_LOCK = threading.Lock()


def get_claim_checker(model_name: str = DEFAULT_MODEL) -> ClaimChecker:
    """Process-level singleton per model name (keeps the model resident)."""
    with _INSTANCES_LOCK:
        inst = _INSTANCES.get(model_name)
        if inst is None:
            inst = ClaimChecker(model_name)
            _INSTANCES[model_name] = inst
        return inst
