"""Library-coverage annotation for Targeted Search candidates (pure logic, no
network / no LLM). The tri-state contract is load-bearing: a false "not in
library" would tell the weekly gap review to re-surface a paper the user
already has, so `in_library=False` may only be set on a *confirmed* miss.

Covers: DOI hit → True + key; unknown DOI → False; arXiv-only lookup; a
candidate with no supported id → None; a reader that raises → None (fail-open);
reader=None → all None; and that `cited_by_count`/coverage survive the
`to_dict`/`from_dict` session round-trip.
"""
from __future__ import annotations

from typing import Any

from zotero_summarizer.services.search._models import Candidate
from zotero_summarizer.services.search.pipeline import _annotate_library_coverage


class _StubReader:
    """Records calls; returns a key for any id present in ``hits`` else None."""

    def __init__(self, hits: dict[str, str]) -> None:
        self.hits = hits
        self.calls: list[tuple[str | None, str | None]] = []

    def find_by_external_id(self, doi: str | None = None, arxiv_id: str | None = None) -> str | None:
        self.calls.append((doi, arxiv_id))
        return self.hits.get(doi or "") or self.hits.get(arxiv_id or "") or None


class _BoomReader:
    def find_by_external_id(self, **_kw: Any) -> str | None:
        raise RuntimeError("zotero db locked")


def test_doi_hit_sets_true_and_key() -> None:
    reader = _StubReader({"10.1/known": "KEY99"})
    cand = Candidate(title="in-lib", doi="10.1/known")
    _annotate_library_coverage([cand], reader)
    assert cand.in_library is True
    assert cand.existing_zotero_key == "KEY99"


def test_unknown_doi_sets_false() -> None:
    reader = _StubReader({})
    cand = Candidate(title="miss", doi="10.1/unknown")
    _annotate_library_coverage([cand], reader)
    assert cand.in_library is False
    assert cand.existing_zotero_key is None


def test_arxiv_only_lookup() -> None:
    reader = _StubReader({"2503.16416": "KEYAX"})
    cand = Candidate(title="arxiv", arxiv_id="2503.16416")
    _annotate_library_coverage([cand], reader)
    assert cand.in_library is True
    assert cand.existing_zotero_key == "KEYAX"
    # DOI-less candidate is looked up by arXiv id only.
    assert reader.calls == [(None, "2503.16416")]


def test_no_supported_id_stays_none() -> None:
    reader = _StubReader({})
    cand = Candidate(title="pmid-only", pmid="123", pmcid="PMC9")
    _annotate_library_coverage([cand], reader)
    assert cand.in_library is None
    assert reader.calls == []  # never queried — nothing to look up on


def test_reader_error_fails_open_to_none() -> None:
    cand = Candidate(title="x", doi="10.1/known")
    _annotate_library_coverage([cand], _BoomReader())
    assert cand.in_library is None  # a failed lookup is NOT a confirmed miss


def test_reader_none_leaves_all_none() -> None:
    cand = Candidate(title="x", doi="10.1/known")
    _annotate_library_coverage([cand], None)
    assert cand.in_library is None


def test_one_reader_across_batch_mixed_states() -> None:
    reader = _StubReader({"10.1/a": "KA", "2409.0001": "KB"})
    cands = [
        Candidate(title="hit-doi", doi="10.1/a"),
        Candidate(title="hit-arxiv", arxiv_id="2409.0001"),
        Candidate(title="miss", doi="10.1/z"),
        Candidate(title="no-id", pmid="7"),
    ]
    _annotate_library_coverage(cands, reader)
    assert [c.in_library for c in cands] == [True, True, False, None]
    assert cands[0].existing_zotero_key == "KA"
    assert cands[1].existing_zotero_key == "KB"
    # Only the three id-bearing candidates hit the reader (no-id skipped).
    assert len(reader.calls) == 3


def test_coverage_and_citations_survive_round_trip() -> None:
    cand = Candidate(title="T", doi="10.1/x", cited_by_count=42)
    cand.in_library = True
    cand.existing_zotero_key = "ABCD1234"
    cand.peer_review = {"tier": "oral", "venue": "ICLR 2025 Oral", "mean_rating": 7.5}
    restored = Candidate.from_dict(cand.to_dict())
    assert restored.cited_by_count == 42
    assert restored.in_library is True
    assert restored.existing_zotero_key == "ABCD1234"
    assert restored.peer_review == {"tier": "oral", "venue": "ICLR 2025 Oral", "mean_rating": 7.5}
