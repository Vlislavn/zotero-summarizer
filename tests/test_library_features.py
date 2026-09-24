"""Leave-one-out (LOO) behaviour of the positive-set P library features.

The bug these guard: `nearest_kept_cosine`/centroids were computed against a P
that included the row being scored, so a positive training row self-matched at
cosine ≈ 1.0 — a leaked "this is positive" tell that vanishes at serve time.
`candidate_row` excludes the whole paper group from vectors and authors.
"""
from __future__ import annotations

import numpy as np
import pytest

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
        paper_groups=tuple(f"key:{key}" for key in keys),
        authors_lower=frozenset(),
        author_tokens=tuple(frozenset() for _ in keys),
        raw_embeddings=raw,
        recent_mask=mask,
    )


def test_loo_drops_self_match_for_a_row_in_P():
    lib = _lib(["A", "B", "C"], [[1, 0, 0], [0, 1, 0], [1, 1, 0]])
    cand = np.asarray([1, 0, 0], dtype=np.float32)  # identical to row A

    nearest_full, *_ = lf.compute_library_features(cand, lib)
    nearest_loo, *_ = lf.compute_library_features(cand, lib, candidate_row={"item_key": "A"})

    assert nearest_full == 1.0  # self-match leak when A is in P
    assert nearest_loo < nearest_full
    assert nearest_loo == np.float32(1 / np.sqrt(2))  # cos to C=[1,1,0]


def test_loo_recomputes_centroid_without_the_row():
    lib = _lib(["A", "B", "C"], [[1, 0, 0], [0, 1, 0], [1, 1, 0]])
    cand = np.asarray([1, 0, 0], dtype=np.float32)
    _, cent_full, _, _, _ = lf.compute_library_features(cand, lib)
    _, cent_loo, _, _, _ = lf.compute_library_features(cand, lib, candidate_row={"item_key": "A"})
    assert cent_loo != cent_full  # excluding A shifts the centroid


def test_no_match_key_is_identical_to_fast_path():
    lib = _lib(["A", "B", "C"], [[1, 0, 0], [0, 1, 0], [1, 1, 0]])
    cand = np.asarray([0.3, 0.7, 0.1], dtype=np.float32)
    full = lf.compute_library_features(cand, lib)
    not_in_p = lf.compute_library_features(cand, lib, candidate_row={"item_key": "NOPE"})
    assert full == not_in_p  # key not in P → no exclusion, same result


def test_excluding_the_only_row_yields_zeros():
    lib = _lib(["A"], [[1, 0, 0]])
    cand = np.asarray([1, 0, 0], dtype=np.float32)
    nearest, cent, recent, drift, _ = lf.compute_library_features(
        cand, lib, candidate_row={"item_key": "A"},
    )
    assert (nearest, cent, recent, drift) == (0.0, 0.0, 0.0, 0.0)


def test_empty_library_returns_zeros():
    empty = lf._empty_library()
    cand = np.zeros(empty.embeddings.shape[1], dtype=np.float32)
    assert lf.compute_library_features(cand, empty, candidate_row={"item_key": "A"}) == (
        0.0, 0.0, 0.0, 0.0, 0.0,
    )


@pytest.mark.parametrize("identity", ["doi", "title"])
def test_group_exclusion_retains_other_papers_and_shared_authors(identity):
    rows = [
        {"item_key": "A", identity: "10.1234/twin" if identity == "doi" else "Twin Paper",
         "authors": "Alice Self; Bob Shared", "days_since_added": "1"},
        {"item_key": "B", identity: "https://doi.org/10.1234/twin" if identity == "doi" else " TWIN  paper ",
         "authors": "Alice Self; Bob Shared", "days_since_added": "1"},
        {"item_key": "C", "title": "Other paper", "authors": "Cathy Shared", "days_since_added": "200"},
        {"item_key": "D", "title": "Recent paper", "authors": "David Other", "days_since_added": "5"},
    ]
    for row in rows:
        row["gold_signal_tier"] = "strong_positive"
    vectors = np.asarray([[1, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float32)
    library = lf.positive_library_from_embeddings(rows, vectors)
    candidate = {**rows[0], "item_key": "unseen-feed-alias"}

    actual = lf.compute_library_features(vectors[0], library, candidate_row=candidate)

    # Only C and D remain: nearest C, centroid [1,2], recent D, one Shared author.
    np.testing.assert_allclose(actual, [1 / np.sqrt(2), 1 / np.sqrt(5), 0, -1 / np.sqrt(5), 1])
    assert lf.compute_library_features(
        vectors[0], library, candidate_row={"item_key": "new", "title": "New", "authors": "Alice Self; Bob Shared"},
    )[-1] == 2


@pytest.mark.parametrize("has_recent", [False, True])
def test_legacy_centroid_only_archive_remains_readable(tmp_path, has_recent):
    from zotero_summarizer.services.model.classifier_artifact import TrainedClassifier
    from zotero_summarizer.services.model.classifier_store import write_archive, load_trained

    model = TrainedClassifier(
        classifier_name="logreg", golden_csv_sha256="legacy", feature_dim=2, pca_dim=0,
        X_train=np.zeros((1, 2)), y_train=np.ones(1),
    )
    # Old pickle state, not new constructor arguments. No group metadata existed.
    model.__dict__.update(
        library_embeddings=np.asarray([[1, 0]], dtype=np.float32),
        library_centroid=np.asarray([1, 0], dtype=np.float32),
        library_authors_lower=frozenset({"shared"}),
    )
    if has_recent:
        model.__dict__["library_recent_centroid"] = np.asarray([0, 1], dtype=np.float32)
    path = tmp_path / "legacy.zip"
    write_archive(model, path)

    loaded = load_trained(path)
    actual = lf.compute_library_features(
        np.asarray([1, 0], dtype=np.float32), loaded._build_predict_library(),
        candidate_row={"item_key": "A", "authors": "Bob Shared"},
    )

    assert actual == (1, 1, 0 if has_recent else 1, -1 if has_recent else 0, 1)
