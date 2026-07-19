"""Targeted Search federation-breadth + agentic-refinement checks (Phases D, E):
the new source leaves' parse shapes, cross-source id dedup through federate, the
query-plan wiring that fires those channels, and the PRF refinement loop (concept
delta → delta federation → union → re-rank, bounded, convergent). Split out of
test_search.py to keep each test module under the LOC ceiling and single-purpose."""
from __future__ import annotations

from zotero_summarizer.services.search._models import (
    Candidate,
    QueryPlan,
    ResearchSession,
    SearchIntent,
)
from zotero_summarizer.services.search.dedup import to_version_families
from zotero_summarizer.services.search.intent import build_query_plan


def _c(title, **kw):
    return Candidate(title=title, **kw)


class _FakeLLM:
    def __init__(self, text):
        self._text = text

    def prompt(self, _prompt):
        return self._text


def _tmp_store(monkeypatch, tmp_path):
    from zotero_summarizer.services.search import session as session_mod

    class _S:
        search_dir = tmp_path

    monkeypatch.setattr(session_mod, "settings", lambda: _S())
    return session_mod


# --- new federation leaves (Phase D): parse shape + dedup-critical ids -------

def test_crossref_hit_parse_shape():
    from zotero_summarizer.integrations.crossref import _hit_from_item

    item = {
        "DOI": "10.1/ABC",
        "title": ["A Study of Things"],
        "abstract": "<jats:p>We show &amp; prove.</jats:p>",
        "author": [{"given": "Ada", "family": "Lovelace"}, {"name": "R. Solo"}],
        "published": {"date-parts": [[2024, 5, 1]]},
        "container-title": ["Journal of Things"],
    }
    h = _hit_from_item(item)
    assert h is not None
    assert h.doi == "10.1/ABC"                      # raw DOI (normalized later at Candidate)
    assert h.title == "A Study of Things"
    assert h.abstract == "We show & prove."         # JATS stripped, entities unescaped
    assert h.authors == ["Ada Lovelace", "R. Solo"]
    assert h.year == 2024
    assert h.venue == "Journal of Things"
    # A row with no title is dropped (can't dedup / display it).
    assert _hit_from_item({"DOI": "10.1/x", "title": []}) is None


def test_semantic_scholar_hit_maps_cross_source_ids():
    from zotero_summarizer.integrations.semantic_scholar import _hit_from_paper

    paper = {
        "title": "Agents in Oncology",
        "abstract": "An abstract.",
        "year": 2025,
        "venue": "Nature",
        "authors": [{"name": "A. One"}, {"name": ""}],
        "externalIds": {"DOI": "10.2/y", "ArXiv": "2501.00001", "PubMed": "39999999"},
        "openAccessPdf": {"url": "https://x/y.pdf"},
    }
    h = _hit_from_paper(paper)
    assert h is not None
    assert (h.doi, h.arxiv_id, h.pmid) == ("10.2/y", "2501.00001", "39999999")  # dedup keys
    assert h.authors == ["A. One"]                  # blank author name dropped
    assert h.is_open_access is True and h.url == "https://x/y.pdf"
    assert _hit_from_paper({"title": ""}) is None    # titleless → dropped


def test_federate_new_channels_contribute_and_dedupe(monkeypatch):
    # Crossref + Semantic Scholar channels feed the pool; a DOI shared across them
    # dedupes into one family, and each source's provenance is kept.
    from zotero_summarizer.integrations.crossref import CrossrefHit
    from zotero_summarizer.integrations.semantic_scholar import SemanticScholarHit
    from zotero_summarizer.services.search import federate as fed

    monkeypatch.setattr(fed, "search_arxiv", lambda *a, **k: [])
    monkeypatch.setattr(fed, "search_europepmc", lambda *a, **k: [])
    monkeypatch.setattr(
        fed, "search_crossref",
        lambda *a, **k: [CrossrefHit(title="Shared", abstract="", doi="10.5/shared"),
                         CrossrefHit(title="CrossOnly", abstract="", doi="10.5/cr")],
    )
    monkeypatch.setattr(
        fed, "search_semantic_scholar",
        lambda *a, **k: [SemanticScholarHit(title="Shared (S2)", abstract="", doi="10.5/shared", arxiv_id="2502.1")],
    )
    plan = QueryPlan(crossref="q", semantic_scholar="q")
    cands = fed.federate(plan, openalex_client=None, quota=5)
    by_doi = {c.doi: c for c in cands}
    assert set(by_doi) == {"10.5/shared", "10.5/cr"}          # shared merged, unique kept
    shared = by_doi["10.5/shared"]
    assert shared.arxiv_id == "2502.1"                         # id filled from the S2 member
    assert {p.source for p in shared.provenance} == {"crossref", "semantic_scholar"}


def test_build_query_plan_wires_crossref_and_semantic_scholar():
    # Regression: both new-source queries must be POPULATED by the plan builder or
    # federate's `if plan.crossref:` / `if plan.semantic_scholar:` never fire (the two
    # Phase D leaves become dead code — not caught by the federate test, which builds
    # a QueryPlan with the fields set by hand).
    intent = SearchIntent(raw_query="q", canonical_question="How X?", concepts=["a", "b"])
    plan = build_query_plan(intent)
    assert plan.crossref                       # keyword bag present
    assert plan.semantic_scholar == "How X?"   # natural-language question
    assert plan.openreview == "How X?"         # same dead-channel guard for the signal-only leaf


# --- OpenReview (signal-only peer-review channel) ---------------------------

def test_openreview_channel_wires_peer_review_signal(monkeypatch):
    # federate() must skip the channel when no client is wired (SIGNAL-ONLY is
    # opt-in, never required), and populate `peer_review` + resolve an OpenAlex id
    # (for cross-source dedup) when a client IS wired — but never touch query_score.
    from types import SimpleNamespace

    from zotero_summarizer.integrations.openreview import OpenReviewHit
    from zotero_summarizer.services.search import federate as fed

    monkeypatch.setattr(fed, "search_arxiv", lambda *a, **k: [])
    monkeypatch.setattr(fed, "search_europepmc", lambda *a, **k: [])
    hit = OpenReviewHit(
        title="Great Paper", abstract="abs", year=2025, venue="ICLR 2025 Oral",
        venueid="ICLR.cc/2025/Conference", tier="oral", mean_rating=7.5, n_reviews=3,
    )
    monkeypatch.setattr(fed, "search_openreview", lambda *a, **k: [hit])
    openalex_client = SimpleNamespace(
        fetch_work_by_title=lambda title, *, year=None: SimpleNamespace(
            openalex_id="https://openalex.org/W123"
        )
    )

    plan = QueryPlan(openreview="q")
    no_client = fed.federate(plan, openalex_client=openalex_client, openreview_client=None, quota=5)
    assert no_client == []  # channel not registered without an openreview_client

    cands = fed.federate(plan, openalex_client=openalex_client, openreview_client=object(), quota=5)
    assert len(cands) == 1
    c = cands[0]
    assert c.openalex_id == "W123"                       # bare id, not the full URL
    assert c.peer_review == {
        "tier": "oral", "venue": "ICLR 2025 Oral", "venueid": "ICLR.cc/2025/Conference",
        "mean_rating": 7.5, "n_reviews": 3,
    }
    assert c.query_score is None                          # SIGNAL-ONLY: no ranking effect
    assert {p.source for p in c.provenance} == {"openreview"}


def test_merge_family_carries_peer_review_from_member():
    # A version family where only the OpenReview member carries the peer-review
    # signal must not lose it when a bibliographically richer non-OpenReview
    # member becomes the preferred version (mirrors correction #12's carry rules).
    or_hit = _c(
        "P", doi="10.1/x", peer_review={"tier": "oral", "venue": "ICLR 2025 Oral"},
    )
    richer = _c(
        "P (journal)", doi="10.1/x", venue="Nature",
        abstract="a much longer abstract that clearly wins the richness sort",
    )
    fams = to_version_families([or_hit, richer])
    assert len(fams) == 1
    assert fams[0].venue == "Nature"                                 # richer version preferred
    assert fams[0].peer_review == {"tier": "oral", "venue": "ICLR 2025 Oral"}  # signal survives


# --- agentic iterative search (Phase E) -------------------------------------

def test_merge_family_preserves_review_and_materialized_state():
    # Correction #12: when an agentic round re-fetches a bibliographically RICHER
    # version (so it wins `_richness` and becomes preferred), the merge must still
    # carry the round-1 member's expensive/irreplaceable derived state — a completed
    # review, the user's verdict, the materialized key, the relevance band/score.
    reviewed = _c(
        "P", arxiv_id="2501.1", query_score=0.8, relevance_band="strong",
        review={"state": "reviewed"}, verdict="read", materialized_zotero_key="ZKEY",
        why=["Strong match"], quality={"grade": "A"},
    )
    richer = _c(
        "P (journal)", arxiv_id="2501.1", doi="10.1/x", venue="Nature",
        abstract="a much longer abstract that clearly wins the richness sort",
    )
    fams = to_version_families([reviewed, richer])
    assert len(fams) == 1
    f = fams[0]
    assert f.doi == "10.1/x" and f.venue == "Nature"   # richer bibliography won preferred
    assert f.review == {"state": "reviewed"}           # ...but derived state survived
    assert f.materialized_zotero_key == "ZKEY"
    assert f.verdict == "read"
    assert f.relevance_band == "strong"
    assert f.query_score == 0.8
    assert f.quality == {"grade": "A"}
    assert f.why == ["Strong match"]


def test_refine_once_empty_delta_on_garbled():
    from zotero_summarizer.services.search.refine import refine_once

    sess = ResearchSession(
        id="x", created_at="t", raw_query="q",
        intent=SearchIntent(raw_query="q", concepts=["a"]), plan=QueryPlan(),
        candidates=[_c("P", query_score=0.5)],
    )
    # An unparseable delta → empty (spec §7 visible-degradation contract), which
    # converges the loop instead of crashing or expanding on garbage.
    assert refine_once(sess, llm=_FakeLLM("not json")) == {"add_concepts": [], "drop_terms": []}


def test_refine_once_parses_and_caps_delta():
    from zotero_summarizer.services.search.refine import refine_once

    sess = ResearchSession(
        id="x", created_at="t", raw_query="q",
        intent=SearchIntent(raw_query="q", concepts=["a"]), plan=QueryPlan(),
        candidates=[_c("P", query_score=0.5)],
    )
    llm = _FakeLLM('{"add_concepts": ["c1","c2","c3","c4","c5"], "drop_terms": ["d1","d2","d3","d4"]}')
    delta = refine_once(sess, llm=llm)
    assert delta["add_concepts"] == ["c1", "c2", "c3", "c4"]  # capped at 4
    assert delta["drop_terms"] == ["d1", "d2", "d3"]          # capped at 3


def test_session_round_trip_preserves_refinements():
    row = {"round": 1, "add_concepts": ["x"], "drop_terms": [], "new_candidates": 2, "pool_size": 9}
    sess = ResearchSession(
        id="rt", created_at="t", raw_query="q",
        intent=SearchIntent(raw_query="q"), plan=QueryPlan(), refinements=[row],
    )
    assert ResearchSession.from_dict(sess.to_dict()).refinements == [row]


def test_run_agentic_rounds_unions_new_and_stops_on_empty_delta(monkeypatch, tmp_path):
    # One round: LLM proposes a concept → delta federation returns a NEW paper → it
    # unions into the pool, gets scored/ranked/banded, and a round summary is recorded.
    # The next round's LLM proposes nothing → the loop converges (no wasted federation).
    from types import SimpleNamespace

    from zotero_summarizer.services.search import refine as refine_mod
    from zotero_summarizer.services.search._relevance import attach_relevance

    store = _tmp_store(monkeypatch, tmp_path)
    pool = [_c(f"P{i}", doi=f"10.1/{i}", query_score=s) for i, s in enumerate([0.9, 0.7, 0.4, 0.2])]
    attach_relevance(pool)
    sess = ResearchSession(
        id="r1", created_at="t", raw_query="topic",
        intent=SearchIntent(raw_query="topic", concepts=["a"]), plan=QueryPlan(),
        candidates=pool, status="reviewing",
    )
    store.save(sess)

    class _SeqLLM:
        def __init__(self, seq):
            self.seq = list(seq)

        def prompt(self, _prompt):
            return self.seq.pop(0)

    llm = _SeqLLM([
        '{"add_concepts": ["new facet"], "drop_terms": ["junk"]}',
        '{"add_concepts": [], "drop_terms": []}',
    ])
    new = _c("NEW", doi="10.9/new")
    monkeypatch.setattr(refine_mod, "federate", lambda *a, **k: [new])

    def _fake_score(_query, cands, *, reranker_model):
        for c in cands:  # keep the pool's scores; give the delta hit a top score
            if c.query_score is None:
                c.query_score = 0.95 if c.candidate_id == "10.9/new" else 0.5

    monkeypatch.setattr(refine_mod, "score_query_relevance", _fake_score)
    deps = SimpleNamespace(
        llm=llm, openalex_client=None, library_finder=None,
        quota=5, crossref_mailto=None, reranker_model="m",
    )
    out = refine_mod.run_agentic_rounds("r1", deps=deps, max_rounds=2)

    assert any(c.candidate_id == "10.9/new" for c in out.candidates)   # unioned in
    assert len(out.refinements) == 1                                    # one productive round
    assert out.refinements[0]["add_concepts"] == ["new facet"]
    assert out.refinements[0]["new_candidates"] == 1
    # persisted under the session lock (a poll would see it)
    assert any(c.candidate_id == "10.9/new" for c in store.load("r1").candidates)
