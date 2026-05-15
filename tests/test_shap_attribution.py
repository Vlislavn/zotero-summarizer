"""Phase 1.14 — TreeSHAP attribution shape and completeness."""
from __future__ import annotations

import numpy as np
import pytest

from zotero_summarizer.services import classifier
from zotero_summarizer.services.classifier_persistence import (
    _EXTRA_FEATURE_NAMES,
    _format_shap,
)


def _row_with_known_values(
    embedding_sum: float = 0.6,
    extras: tuple[float, ...] = (0.1, 0.05, -0.02, 0.0, 0.0, 0.4, 0.08),
    bias: float = -0.5,
) -> np.ndarray:
    """Synthetic SHAP row: known embedding-bucket sum + named extras + bias."""
    assert len(extras) == len(_EXTRA_FEATURE_NAMES)
    n = classifier.EMBEDDING_DIM + len(_EXTRA_FEATURE_NAMES) + 1
    row = np.zeros(n, dtype=np.float32)
    # Spread embedding_sum across two dims to prove the function aggregates.
    row[0] = embedding_sum / 2
    row[1] = embedding_sum / 2
    for i, v in enumerate(extras):
        row[classifier.EMBEDDING_DIM + i] = v
    row[-1] = bias
    return row


def test_format_shap_returns_one_entry_per_feature_plus_bias():
    out = _format_shap(_row_with_known_values())
    features = {c["feature"] for c in out}
    expected = {"semantic_match_specter2", "bias", *_EXTRA_FEATURE_NAMES}
    assert features == expected, "every named feature + semantic bucket + bias must appear once"


def test_format_shap_aggregates_embedding_dimensions():
    out = _format_shap(_row_with_known_values(embedding_sum=1.25))
    bucket = next(c for c in out if c["feature"] == "semantic_match_specter2")
    assert bucket["contribution"] == pytest.approx(1.25, abs=1e-5)


def test_format_shap_preserves_extra_feature_values():
    extras = (0.1, 0.05, -0.02, 0.07, 0.04, 0.4, 0.08)
    out = _format_shap(_row_with_known_values(extras=extras))
    by_name = {c["feature"]: c["contribution"] for c in out}
    for i, name in enumerate(_EXTRA_FEATURE_NAMES):
        assert by_name[name] == pytest.approx(float(extras[i]), abs=1e-5)


def test_format_shap_sorted_by_absolute_contribution():
    out = _format_shap(_row_with_known_values(
        embedding_sum=0.1,
        extras=(0.0, 0.0, 0.0, 0.0, 0.0, 0.42, 0.0),
        bias=-0.9,
    ))
    abs_vals = [abs(c["contribution"]) for c in out]
    assert abs_vals == sorted(abs_vals, reverse=True), \
        "format_shap must order by |contribution| descending"


def test_format_shap_completeness_matches_logit():
    """TreeSHAP property: sum(contributions) ≈ raw model logit."""
    out = _format_shap(_row_with_known_values(
        embedding_sum=0.6,
        extras=(0.1, 0.05, -0.02, 0.0, 0.0, 0.4, 0.08),
        bias=-0.5,
    ))
    total = sum(c["contribution"] for c in out)
    # Embedding bucket (0.6) + extras (0.61) + bias (-0.5) = 0.71
    assert total == pytest.approx(0.71, abs=1e-5)


def test_format_shap_rejects_wrong_length():
    with pytest.raises(ValueError, match="expected"):
        _format_shap(np.zeros(10, dtype=np.float32))
