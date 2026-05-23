"""Leave-one-out (LOO) behaviour of the positive-set P library features.

The bug these guard: `nearest_kept_cosine`/centroids were computed against a P
that included the row being scored, so a positive training row self-matched at
cosine ≈ 1.0 — a leaked "this is positive" tell that vanishes at serve time.
`exclude_item_key` drops the row's own embedding so train and serve agree.
"""
from __future__ import annotations

import numpy as np

from zotero_summarizer.services.model import library_features as lf


def _lib(keys, vectors, recent=None):
    """Build a PositiveLibrary directly from toy embeddings (any dim)."""
    raw = np.asarray(vectors, dtype=np.float32)
    mask = np.asarray(recent if recent is not None else [True] * len(keys), dtype=bool)
    recent_centroid = (
        lf._l2_normalise(raw[mask].mean(axis=0)).astype(np.float32)
        if mask.any() else lf._l2_normalise(raw.mean(axis=0)).astype(np.float32)
    )
    return lf.PositiveLibrary(
        embeddings=lf._l2_normalise(raw).astype(np.float32),
        centroid=lf._l2_normalise(raw.mean(axis=0)).astype(np.float32),
        recent_centroid=recent_centroid,
        item_keys=tuple(keys),
        authors_lower=frozenset(),
        raw_embeddings=raw,
        recent_mask=mask,
    )


def test_loo_drops_self_match_for_a_row_in_P():
    lib = _lib(["A", "B", "C"], [[1, 0, 0], [0, 1, 0], [1, 1, 0]])
    cand = np.asarray([1, 0, 0], dtype=np.float32)  # identical to row A

    nearest_full, *_ = lf.compute_library_features(cand, lib)
    nearest_loo, *_ = lf.compute_library_features(cand, lib, exclude_item_key="A")

    assert nearest_full == 1.0  # self-match leak when A is in P
    assert nearest_loo < nearest_full
    assert nearest_loo == np.float32(1 / np.sqrt(2))  # cos to C=[1,1,0]


def test_loo_recomputes_centroid_without_the_row():
    lib = _lib(["A", "B", "C"], [[1, 0, 0], [0, 1, 0], [1, 1, 0]])
    cand = np.asarray([1, 0, 0], dtype=np.float32)
    _, cent_full, _, _, _ = lf.compute_library_features(cand, lib)
    _, cent_loo, _, _, _ = lf.compute_library_features(cand, lib, exclude_item_key="A")
    assert cent_loo != cent_full  # excluding A shifts the centroid


def test_no_match_key_is_identical_to_fast_path():
    lib = _lib(["A", "B", "C"], [[1, 0, 0], [0, 1, 0], [1, 1, 0]])
    cand = np.asarray([0.3, 0.7, 0.1], dtype=np.float32)
    full = lf.compute_library_features(cand, lib)
    not_in_p = lf.compute_library_features(cand, lib, exclude_item_key="NOPE")
    assert full == not_in_p  # key not in P → no exclusion, same result


def test_excluding_the_only_row_yields_zeros():
    lib = _lib(["A"], [[1, 0, 0]])
    cand = np.asarray([1, 0, 0], dtype=np.float32)
    nearest, cent, recent, drift, _ = lf.compute_library_features(
        cand, lib, exclude_item_key="A",
    )
    assert (nearest, cent, recent, drift) == (0.0, 0.0, 0.0, 0.0)


def test_empty_library_returns_zeros():
    empty = lf._empty_library()
    cand = np.zeros(empty.embeddings.shape[1], dtype=np.float32)
    assert lf.compute_library_features(cand, empty, exclude_item_key="A") == (
        0.0, 0.0, 0.0, 0.0, 0.0,
    )
