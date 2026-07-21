"""Version-family resolution — the expert review's dedup fix (spec §11).

A single ``external_id`` is insufficient: the same work arrives from arXiv (as a
preprint), OpenAlex (with a DOI), and Europe PMC (with a PMID). Union candidates
that share ANY normalized identifier into one family, pick a preferred version,
merge provenance, and OR the integrity flags across members. Never merge on title
alone (``dedup_doi_arxiv_only`` memory) — candidates with no shared identifier
stay separate even if titles look similar.

A preprint and its journal article are one family with a preferred version, NOT
destructively merged — they can differ materially; the merge keeps every source's
provenance so a missed-paper analysis can see all of them.
"""
from __future__ import annotations

from zotero_summarizer.services.search._models import Candidate


class _Union:
    """Tiny union-find over hashable keys (identifier tokens)."""

    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def _version_type(cand: Candidate) -> str:
    if cand.is_retracted:
        return "retraction"
    if cand.doi and cand.venue:
        return "version_of_record"
    if cand.arxiv_id and not cand.doi:
        return "preprint"
    return "unknown"


def _richness(cand: Candidate) -> tuple[int, int, int, int]:
    """Sort key for picking a family's preferred version: prefer a member with an
    abstract, a DOI, a journal venue, then the longest abstract."""
    return (bool(cand.abstract), bool(cand.doi), bool(cand.venue), len(cand.abstract))


def _merge_family(members: list[Candidate]) -> Candidate:
    preferred = max(members, key=_richness)
    # Fill missing identifiers + flags from the other members (one work, many ids).
    for m in members:
        preferred.doi = preferred.doi or m.doi
        preferred.arxiv_id = preferred.arxiv_id or m.arxiv_id
        preferred.pmid = preferred.pmid or m.pmid
        preferred.pmcid = preferred.pmcid or m.pmcid
        preferred.openalex_id = preferred.openalex_id or m.openalex_id
        preferred.abstract = preferred.abstract or m.abstract
        preferred.venue = preferred.venue or m.venue
        preferred.url = preferred.url or m.url
        preferred.year = preferred.year or m.year
        preferred.is_open_access = preferred.is_open_access or m.is_open_access
        preferred.is_retracted = preferred.is_retracted or m.is_retracted
        # Correction #12: carry DERIVED state (expensive light/deep review, the user's
        # reading intent, and the materialized library key) from whichever member holds
        # it. Without this, an agentic round that re-fetches a bibliographically richer
        # version (so it wins `_richness`) would silently discard a completed review or
        # a materialized key that lived on the round-1 candidate.
        preferred.query_score = preferred.query_score if preferred.query_score is not None else m.query_score
        preferred.goal_sim = preferred.goal_sim if preferred.goal_sim is not None else m.goal_sim
        preferred.cited_by_count = (
            preferred.cited_by_count if preferred.cited_by_count is not None else m.cited_by_count
        )
        preferred.quality = preferred.quality or m.quality
        preferred.coverage = preferred.coverage or m.coverage
        preferred.peer_review = preferred.peer_review or m.peer_review
        preferred.review = preferred.review or m.review
        preferred.verdict = preferred.verdict or m.verdict
        preferred.relevance_band = preferred.relevance_band or m.relevance_band
        preferred.why = preferred.why or m.why
        preferred.materialized_zotero_key = preferred.materialized_zotero_key or m.materialized_zotero_key
        if preferred.in_library is None:
            preferred.in_library = m.in_library
        preferred.existing_zotero_key = preferred.existing_zotero_key or m.existing_zotero_key
        if m is not preferred:
            preferred.provenance.extend(m.provenance)
    preferred.version_type = _version_type(preferred)
    preferred.version_family_id = preferred.candidate_id
    return preferred


def to_version_families(candidates: list[Candidate]) -> list[Candidate]:
    """Collapse candidates sharing any identifier into version families. Returns
    one merged Candidate per family, order-preserving on first appearance. Members
    with NO identifiers are kept as-is (never title-merged)."""
    uf = _Union()
    for cand in candidates:
        keys = cand.identifier_keys()
        for k in keys[1:]:
            uf.union(keys[0], k)

    groups: dict[str, list[Candidate]] = {}
    order: list[str] = []
    for cand in candidates:
        keys = cand.identifier_keys()
        group_key = uf.find(keys[0]) if keys else f"solo:{id(cand)}"
        if group_key not in groups:
            groups[group_key] = []
            order.append(group_key)
        groups[group_key].append(cand)

    return [_merge_family(groups[k]) for k in order]


__all__ = ["to_version_families"]
