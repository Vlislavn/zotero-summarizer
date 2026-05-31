"""Prestige/quality floor on the top bands (must_read/should_read).

Covers the pure demote-one-band helper (domain.apply_prestige_floor), the
data-driven median-of-known floor derivation, and the histogram by_band tally
reflecting the floor. Demote-only; unknown/cold-start prestige is never
penalised."""
from __future__ import annotations

from types import SimpleNamespace

from zotero_summarizer.domain import apply_prestige_floor
from zotero_summarizer.services.library import reading_queue as rq
from zotero_summarizer.services.model.prestige import percentile_to_score


# --- apply_prestige_floor (pure) -------------------------------------------

def test_demotes_top_bands_one_step_when_known_low():
    assert apply_prestige_floor("must_read", 0.1, prestige_known=True, floor=0.5) == "should_read"
    assert apply_prestige_floor("should_read", 0.1, prestige_known=True, floor=0.5) == "could_read"


def test_keeps_when_prestige_clears_floor():
    assert apply_prestige_floor("must_read", 0.9, prestige_known=True, floor=0.5) == "must_read"
    assert apply_prestige_floor("must_read", 0.5, prestige_known=True, floor=0.5) == "must_read"  # >= keeps


def test_never_penalises_missing_evidence():
    # unknown prestige → keep; no floor → keep; None score → keep.
    assert apply_prestige_floor("must_read", 0.1, prestige_known=False, floor=0.5) == "must_read"
    assert apply_prestige_floor("must_read", 0.1, prestige_known=True, floor=None) == "must_read"
    assert apply_prestige_floor("must_read", None, prestige_known=True, floor=0.5) == "must_read"


def test_low_bands_never_demoted():
    assert apply_prestige_floor("could_read", 0.0, prestige_known=True, floor=0.5) == "could_read"
    assert apply_prestige_floor("dont_read", 0.0, prestige_known=True, floor=0.5) == "dont_read"


# --- floor derivation (data-driven, median of KNOWN prestige) --------------

def test_floor_is_median_of_known_prestige():
    pairs = [(0.1, True), (0.5, True), (0.9, True), (0.3, False), (None, True)]
    assert rq.prestige_floor(pairs) == 0.5   # median of known {0.1,0.5,0.9}; unknown/None ignored


def test_floor_none_when_no_known_prestige():
    assert rq.prestige_floor([(0.4, False), (None, False)]) is None
    assert rq.prestige_floor([]) is None


# --- histogram by_band reflects the floor ----------------------------------

def test_distribution_by_band_applies_floor():
    records = [
        {"relevance_score": 4.8, "prestige_score": 0.1, "prestige_known": True},   # demote → should
        {"relevance_score": 4.8, "prestige_score": 0.9, "prestige_known": True},   # keep must
        {"relevance_score": 4.8, "prestige_score": 0.1, "prestige_known": False},  # unknown → keep must
    ]
    dist = rq._score_distribution(records, floor=0.5)
    assert dist["by_band"]["must_read"] == 2     # the 0.9 + the unknown
    assert dist["by_band"]["should_read"] == 1   # the demoted low-prestige one
    assert dist["prestige_floor"] == 0.5
    # X-axis bins stay by raw score: all three sit in the 4.5–5.0 bin.
    assert dist["bins"][-1]["count"] == 3


def test_distribution_no_floor_is_pure_score_bands():
    records = [{"relevance_score": 4.8, "prestige_score": 0.1, "prestige_known": True}]
    dist = rq._score_distribution(records, floor=None)
    assert dist["by_band"]["must_read"] == 1     # no floor → unchanged


# --- plumbing: percentile → scoring → _entry_prestige ----------------------

def _pred(aux: dict) -> SimpleNamespace:
    return SimpleNamespace(raw_score=4.2, aux_context=aux, shap_contribs=[])


def test_scoring_from_prediction_derives_prestige_from_percentile():
    """prestige_score comes from citation_percentile via the SHARED mapping —
    NOT reconstructed from h-index — and percentile is recorded as an input."""
    sc = rq.scoring_from_prediction(_pred({"citation_percentile": 0.9, "max_author_h_index": 200}))
    assert sc["prestige_score"] == percentile_to_score(0.9)        # == 4.6, ignores the h=200
    assert sc["prestige_inputs"]["citation_percentile"] == 0.9


def test_scoring_from_prediction_cold_start_prestige_is_none():
    """No percentile (cold-start) → prestige_score None even with a big h-index,
    so the floor treats it as unknown and never demotes it."""
    sc = rq.scoring_from_prediction(_pred({"citation_percentile": None, "max_author_h_index": 80}))
    assert sc["prestige_score"] is None
    assert "citation_percentile" not in (sc["prestige_inputs"] or {})


def test_entry_prestige_known_iff_percentile_present():
    """``known`` keys off the field-normalized percentile, not h-index>0."""
    known = rq._entry_prestige({"scoring": {"prestige_score": 4.6, "prestige_inputs": {"citation_percentile": 0.9}}})
    assert known == (4.6, True)
    # h-index present but NO percentile → unknown (cold-start), floor won't penalise.
    unknown = rq._entry_prestige({"scoring": {"prestige_score": None, "prestige_inputs": {"max_author_h_index": 80}}})
    assert unknown == (None, False)
