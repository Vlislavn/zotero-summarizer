"""MiniCheck encoder claim-checker wrapper: scoring alignment + the
optional-dependency / inference degradation boundaries (Phase A shadow)."""
from __future__ import annotations

from zotero_summarizer.services.model import claim_checker as cc


class _FakeScorer:
    """Mimics minicheck.MiniCheck.score(docs, claims) -> (labels, probs, _, _)."""

    def __init__(self, prob=0.8):
        self._prob = prob

    def score(self, docs, claims):
        n = len(claims)
        return [1] * n, [self._prob] * n, None, None


def test_hf_repo_for_maps_variants_and_falls_back():
    assert cc.hf_repo_for("flan-t5-large") == "lytang/MiniCheck-Flan-T5-Large"
    assert cc.hf_repo_for("deberta-v3-large") == "lytang/MiniCheck-DeBERTa-v3-Large"
    assert cc.hf_repo_for("nonexistent") == cc.MINICHECK_REPOS[cc.DEFAULT_MODEL]


def test_score_returns_aligned_probs():
    chk = cc.ClaimChecker("flan-t5-large")
    chk._scorer = _FakeScorer(0.73)  # bypass load
    out = chk.score(["claim a", "claim b"], ["evidence a", "evidence b"])
    assert out == [0.73, 0.73] and chk.is_ready()


def test_score_none_when_dependency_absent():
    # _load failed (e.g. minicheck not installed) → score degrades to None so the
    # caller keeps the LLM verdict; never raises.
    chk = cc.ClaimChecker("flan-t5-large")
    chk._load_failed = True
    assert chk.score(["c"], ["e"]) is None


def test_score_none_on_mismatched_lengths():
    chk = cc.ClaimChecker("flan-t5-large")
    chk._scorer = _FakeScorer()
    assert chk.score(["c"], ["e1", "e2"]) is None
    assert chk.score([], []) is None


def test_score_none_when_inference_raises():
    class _Boom:
        def score(self, docs, claims):
            raise RuntimeError("torch boom")

    chk = cc.ClaimChecker("flan-t5-large")
    chk._scorer = _Boom()
    assert chk.score(["c"], ["e"]) is None  # inference boundary: never breaks the eval


def test_get_claim_checker_is_singleton_per_model():
    a = cc.get_claim_checker("flan-t5-large")
    b = cc.get_claim_checker("flan-t5-large")
    assert a is b
