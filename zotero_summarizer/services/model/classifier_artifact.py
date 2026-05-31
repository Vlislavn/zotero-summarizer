"""The serialisable TrainedClassifier artefact + SHAP attribution."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from zotero_summarizer.services.model import classifier

DEFAULT_MODEL_DIR = Path.home() / ".cache" / "zotero-summarizer" / "models"

# Per-item aux (corpus affinity + OpenAlex prestige) is I/O-bound — run it
# across items concurrently to overlap the OpenAlex network latency. This is
# network/CPU I/O, separate from the local-LLM serial rule; OpenAlex's own
# rate-limiter still bounds outbound calls.
_AUX_WORKERS = 8


# Human-readable names for the 12 tabular extras (order must match
# ``_extra_features`` in classifier.py).
_EXTRA_FEATURE_NAMES = (
    "has_doi", "has_venue", "year_recency",
    "title_log_len", "abstract_log_len",
    "corpus_affinity", "prestige_score",
    "nearest_kept_cosine", "positive_centroid_cosine",
    "recent_centroid_cosine", "topic_drift",
    "author_overlap_count",
)


def _format_shap(
    row: np.ndarray, *, embedding_dim: int = classifier.EMBEDDING_DIM
) -> list[dict[str, float]]:
    """Collapse a TreeSHAP row into a UI-friendly summary.

    LightGBM's ``predict(X, pred_contrib=True)`` returns a matrix of shape
    ``(n_samples, n_features + 1)`` — the last column is the bias
    (expected_value). We bucket the leading ``embedding_dim`` embedding
    contributions into one ``semantic_match_specter2`` value (their sum),
    keep the named extras individually, surface the bias separately, and
    return the list sorted by ``|contribution|`` descending.

    ``embedding_dim`` is the size of the embedding block the model actually
    saw: 768 for a full-feature model, or the PCA component count for a
    Sprint-3b PCA-baked LightGBM. The trailing extras keep their names
    regardless, since PCA only reduces the embedding block.
    """
    n_extras = len(_EXTRA_FEATURE_NAMES)
    expected_total = embedding_dim + n_extras + 1   # +1 for bias
    if row.shape[0] != expected_total:
        raise ValueError(
            f"_format_shap expected length {expected_total}, got {row.shape[0]}"
        )
    semantic = float(row[:embedding_dim].sum())
    bias = float(row[-1])
    out: list[dict[str, float]] = [
        {"feature": "semantic_match_specter2", "contribution": semantic},
        {"feature": "bias", "contribution": bias},
    ]
    for idx, name in enumerate(_EXTRA_FEATURE_NAMES):
        out.append({
            "feature": name,
            "contribution": float(row[embedding_dim + idx]),
        })
    out.sort(key=lambda c: abs(c["contribution"]), reverse=True)
    return out


@dataclass
class TrainedClassifier:
    """A serialisable, ready-to-predict classifier for the hybrid gate.

    For LightGBM/LogReg we store the fitted sklearn model in ``model_payload``
    and predict directly. For TabPFN — which does in-context learning — we
    store ``(X_train, y_train, pca_object)`` and re-fit at predict time
    (cheap-ish on the fitted PCA basis).
    """

    classifier_name: str           # "tabpfn" | "lightgbm" | "logreg"
    golden_csv_sha256: str          # full sha (not prefix) for invalidation
    feature_dim: int                # 777 = 768 SPECTER2 + 9 extras (Sprint 1)
    pca_dim: int                    # only meaningful for TabPFN
    # Training payload — what we need to predict
    X_train: np.ndarray             # (n_train, feature_dim) float32
    y_train: np.ndarray             # (n_train,) float64 — continuous relevance label
    pca_object: Any = None          # sklearn PCA, only set for TabPFN
    fitted_model: Any = None        # sklearn LGBMRegressor / Ridge
    calibrator: Any = None          # legacy field, always None after Sprint 1
    # Legacy threshold fields, kept as zeros for joblib backward compat.
    t_keep: float = 0.0
    t_must: float = 0.0
    t_could: float = 0.0
    # Library-conditioned feature payload (Sprint 1 + Sprint 2).
    library_embeddings: np.ndarray | None = None  # (n_P, EMBEDDING_DIM) L2-normalised
    library_centroid: np.ndarray | None = None    # (EMBEDDING_DIM,) L2-normalised
    library_recent_centroid: np.ndarray | None = None  # mean(P ∩ last 90d), L2-norm
    library_authors_lower: frozenset[str] | None = None  # surnames in P, lower-case
    # Training metadata
    training_metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ predict

    def predict(
        self,
        items: list[dict[str, str]],
        *,
        corpus_db_path: Path,
        goals_config: Any,
        progress_cb: Callable[[int, int], None] | None = None,
        return_shap: bool = False,
        prestige_network: bool = True,
    ) -> list[classifier.FeedPrediction]:
        """Featurise + predict a batch of items.

        Returns a parallel list of FeedPrediction objects (same shape as
        ``classifier.predict_new_items``). When ``return_shap=True`` and the
        underlying model is LightGBM, ``pred.shap_contribs`` is populated via
        TreeSHAP (``predict_proba(X, pred_contrib=True)``); ``pred.aux_context``
        is populated for all model types.
        """
        valid = [
            it for it in items
            if (it.get("title") or "").strip() and (it.get("abstract") or "").strip()
        ]
        if not valid:
            return []

        # 1. Featurise — same as the prediction path in predict_new_items.
        embed_cache, openalex_client = classifier._build_aux_providers(
            corpus_db_path, goals_config, allow_network=prestige_network,
        )
        from zotero_summarizer.services.model.library_features import (
            PositiveLibrary,
            compute_library_features,
        )
        zero_centroid = np.zeros(classifier.EMBEDDING_DIM, dtype=np.float32)
        if self.library_embeddings is not None and self.library_centroid is not None:
            # Persisted P stores only normalised embeddings + centroids (no
            # item_keys), so LOO is inert here — which is correct: a brand-new
            # item being scored is never in P. raw_embeddings/recent_mask are
            # unused on this path but required by the dataclass.
            library = PositiveLibrary(
                embeddings=self.library_embeddings,
                centroid=self.library_centroid,
                recent_centroid=(
                    self.library_recent_centroid
                    if self.library_recent_centroid is not None
                    else self.library_centroid
                ),
                item_keys=tuple(),
                authors_lower=self.library_authors_lower or frozenset(),
                raw_embeddings=self.library_embeddings,
                recent_mask=np.zeros(self.library_embeddings.shape[0], dtype=bool),
            )
        else:
            library = PositiveLibrary(
                embeddings=np.zeros((0, classifier.EMBEDDING_DIM), dtype=np.float32),
                centroid=zero_centroid,
                recent_centroid=zero_centroid,
                item_keys=tuple(),
                authors_lower=frozenset(),
                raw_embeddings=np.zeros((0, classifier.EMBEDDING_DIM), dtype=np.float32),
                recent_mask=np.zeros((0,), dtype=bool),
            )
        X_new = np.zeros((len(valid), self.feature_dim), dtype=np.float32)
        aux_contexts: list[dict[str, float]] = []
        # Embed the whole batch in ONE (batched, GPU-accelerated) pass instead of
        # N single-item forwards — the gate's per-tick throughput win. Cache hits
        # are reused; only misses hit the encoder.
        cache_keys = [
            str(it.get("item_key") or it.get("item_id") or f"item_{i}")
            for i, it in enumerate(valid)
        ]
        embeddings = classifier.get_or_compute_embeddings_batch(
            corpus_db_path,
            [
                {"item_key": cache_keys[i],
                 "title": (it.get("title") or "").strip(),
                 "abstract": (it.get("abstract") or "").strip()}
                for i, it in enumerate(valid)
            ],
        )
        # Aux (corpus affinity + OpenAlex prestige) is independent per item and
        # I/O-bound, so compute it for the whole batch CONCURRENTLY — overlaps the
        # OpenAlex network latency that dominates per-item cost. Safe: the caches
        # open a fresh sqlite connection per call and OpenAlex is rate-limited.
        def _aux_for(it: dict[str, str]) -> tuple[float, float, dict[str, float]]:
            yr = (it.get("publication_date") or it.get("year") or "")[:4]
            return classifier._compute_aux_with_context(
                embed_cache, openalex_client,
                title=(it.get("title") or "").strip(),
                abstract=(it.get("abstract") or "").strip(),
                doi=(it.get("doi") or "").strip(),
                year=int(yr) if yr.isdigit() else None,
            )

        if len(valid) > 1 and (embed_cache is not None or openalex_client is not None):
            with ThreadPoolExecutor(max_workers=min(_AUX_WORKERS, len(valid))) as pool:
                aux_results = list(pool.map(_aux_for, valid))
        else:
            aux_results = [_aux_for(it) for it in valid]

        for i, it in enumerate(valid):
            title = (it.get("title") or "").strip()
            abstract = (it.get("abstract") or "").strip()
            venue = (it.get("publication_title") or it.get("venue") or "").strip()
            cache_key = cache_keys[i]
            emb = embeddings[i]
            X_new[i, :classifier.EMBEDDING_DIM] = emb
            doi = (it.get("doi") or "").strip()
            year_str = (it.get("publication_date") or it.get("year") or "")[:4]
            affinity, prestige, ctx = aux_results[i]
            aux_contexts.append(ctx)
            authors_str = (it.get("authors") or "").strip()
            nearest, centroid, recent, drift, authors_overlap = compute_library_features(
                emb, library, candidate_authors=authors_str, exclude_item_key=cache_key,
            )
            feature_row = {"doi": doi, "venue": venue, "year": year_str}
            X_new[i, classifier.EMBEDDING_DIM:] = classifier._extra_features(
                feature_row, title, abstract,
                corpus_affinity=affinity, prestige_score=prestige,
                nearest_kept_cosine=nearest, positive_centroid_cosine=centroid,
                recent_centroid_cosine=recent, topic_drift=drift,
                author_overlap_count=authors_overlap,
            )
            if progress_cb is not None and (i + 1) % 10 == 0:
                progress_cb(i + 1, len(valid))

        # 2. Raw predict — uses pre-fitted sklearn model or re-fits TabPFN.
        p_raw = self._raw_predict(X_new)

        # 2b. SHAP (optional, LightGBM only — TreeSHAP via pred_contrib=True).
        shap_per_item: list[list[dict[str, float]] | None] = [None] * len(valid)
        if return_shap and self.classifier_name == "lightgbm" and self.fitted_model is not None:
            # TreeSHAP must run on the SAME matrix the model was fit on. For a
            # PCA-baked model that's the reduced (n_pca + extras) matrix, not
            # the raw 780-wide one — otherwise pred_contrib raises a feature
            # mismatch. The embedding block size shrinks to n_pca accordingly.
            X_model = self._model_input(X_new)
            emb_block = X_model.shape[1] - len(_EXTRA_FEATURE_NAMES)
            contribs = classifier.predict_named(
                self.fitted_model, X_model, pred_contrib=True
            )
            for i in range(len(valid)):
                shap_per_item[i] = _format_shap(contribs[i], embedding_dim=emb_block)

        # 3. Score → priority. Regression output is the continuous relevance
        # in [1, 5]; deterministic bucketing via `domain.score_to_priority`
        # produces the four-class label kept for UI / Zotero-note compat.
        from zotero_summarizer.domain import score_to_priority

        p_clip = np.clip(p_raw, 1.0, 5.0)
        predictions: list[classifier.FeedPrediction] = []
        for i, (it, raw, score) in enumerate(zip(valid, p_raw, p_clip)):
            title = (it.get("title") or "").strip()
            abstract = (it.get("abstract") or "").strip()
            preview = abstract[:200].rstrip()
            if len(abstract) > 200:
                preview += "…"
            s = float(score)
            predictions.append(classifier.FeedPrediction(
                item_key=str(it.get("item_key") or it.get("item_id") or ""),
                title=title,
                authors=(it.get("authors") or "").strip(),
                venue=(it.get("publication_title") or it.get("venue") or "").strip(),
                doi=(it.get("doi") or "").strip(),
                abstract_preview=preview,
                raw_score=float(raw),
                calibrated_score=s / 5.0,
                predicted_priority=score_to_priority(s),
                shap_contribs=shap_per_item[i],
                aux_context=aux_contexts[i],
            ))
        return predictions

    def _model_input(self, X_new: np.ndarray) -> np.ndarray:
        """Map raw FEATURE_DIM-wide features to the matrix the fitted model
        expects. A Sprint-3b LightGBM bakes in a PCA over the embedding block,
        so reduce it the same way training did; other models pass through.
        """
        if self.pca_object is not None and self.classifier_name == "lightgbm":
            emb_red = self.pca_object.transform(X_new[:, :classifier.EMBEDDING_DIM])
            return np.concatenate(
                [emb_red, X_new[:, classifier.EMBEDDING_DIM:]], axis=1
            ).astype(np.float32)
        return X_new

    def _raw_predict(self, X_new: np.ndarray) -> np.ndarray:
        """Model-specific predict, returning a 1-D array of relevance scores in [1, 5]
        (clipping is the caller's responsibility)."""
        if self.classifier_name == "tabpfn":
            from tabpfn import TabPFNRegressor

            X_train_red = self.pca_object.transform(self.X_train[:, :classifier.EMBEDDING_DIM])
            X_new_red = self.pca_object.transform(X_new[:, :classifier.EMBEDDING_DIM])
            X_train_full = np.concatenate(
                [X_train_red, self.X_train[:, classifier.EMBEDDING_DIM:]], axis=1
            ).astype(np.float32)
            X_new_full = np.concatenate(
                [X_new_red, X_new[:, classifier.EMBEDDING_DIM:]], axis=1
            ).astype(np.float32)
            reg = TabPFNRegressor(
                n_estimators=8, device="auto",
                ignore_pretraining_limits=False, random_state=42,
            )
            reg.fit(X_train_full, self.y_train)
            return reg.predict(X_new_full)
        if self.classifier_name in ("lightgbm", "logreg"):
            assert self.fitted_model is not None, (
                f"fitted_model missing for {self.classifier_name}; bug?"
            )
            return classifier.predict_named(
                self.fitted_model, self._model_input(X_new)
            )
        raise ValueError(f"unknown classifier_name {self.classifier_name!r}")

