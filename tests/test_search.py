"""Targeted Search pure-logic checks (no network / no LLM): the constrained
re-rank guarantee, version-family dedup, deep-set selection, per-source query
plan, and session JSON round-trip. These are the load-bearing invariants — the
ranking contract is the whole reason the feature exists (spec §8)."""
from __future__ import annotations

from zotero_summarizer.services.search._models import (
    Candidate,
    QueryPlan,
    ResearchSession,
    SearchIntent,
)
from zotero_summarizer.services.search import review as review_mod
from zotero_summarizer.services.search import _targeted_review as tr_mod
from zotero_summarizer.services.search.dedup import to_version_families
from zotero_summarizer.services.search.intent import build_query_plan, parse_intent
from zotero_summarizer.services.search.rank import constrained_key, rank_candidates
from zotero_summarizer.services.search.review import light_review, select_deep_set
from zotero_summarizer.services.search._targeted_review import targeted_review


def _c(title, **kw):
    return Candidate(title=title, **kw)


# --- version families -------------------------------------------------------

def test_shared_doi_merges_into_one_family():
    a = _c("Paper", doi="10.1/x", arxiv_id="2401.00001")
    b = _c("Paper (journal)", doi="10.1/x", venue="Nature", abstract="richer abstract")
    fams = to_version_families([a, b])
    assert len(fams) == 1
    fam = fams[0]
    assert fam.doi == "10.1/x"
    assert fam.arxiv_id == "2401.00001"  # filled from the preprint member
    assert fam.venue == "Nature"


def test_distinct_ids_never_title_merge():
    a = _c("Deep learning for X", doi="10.1/a")
    b = _c("Deep learning for X", doi="10.1/b")  # same title, different DOI
    assert len(to_version_families([a, b])) == 2


def test_retraction_flag_ors_across_family():
    a = _c("P", doi="10.1/x")
    b = _c("P", doi="10.1/x", is_retracted=True)
    fams = to_version_families([a, b])
    assert len(fams) == 1 and fams[0].is_retracted is True


# --- constrained re-rank contract (spec §8) --------------------------------

def test_quality_cannot_cross_a_relevance_bucket():
    # High relevance, no quality vs low relevance, top quality: relevance wins.
    hi = _c("hi", query_score=5.0)
    lo = _c("lo", query_score=1.0, quality={"quality_band": "highlight", "grade": "A"})
    rank_candidates([lo, hi])  # sort in place
    ordered = rank_candidates([lo, hi])
    assert ordered[0] is hi, "a top-quality low-relevance paper must not outrank a high-relevance one"


def test_quality_reorders_within_a_bucket():
    # Same relevance bucket (within EPSILON): quality breaks the tie.
    a = _c("a", query_score=3.00, quality={"quality_band": "", "grade": "C"})
    b = _c("b", query_score=3.10, quality={"quality_band": "highlight", "grade": "A"})
    ordered = rank_candidates([a, b])
    assert ordered[0] is b


def test_retracted_sinks_to_the_bottom():
    good = _c("good", query_score=0.1)
    bad = _c("retracted", query_score=9.0, is_retracted=True)
    ordered = rank_candidates([bad, good])
    assert ordered[-1] is bad
    assert constrained_key(bad, epsilon=0.5)[0] == float("-inf")


# --- deep-set selection -----------------------------------------------------

def test_select_deep_set_composition_and_k():
    cands = [
        _c("r1", query_score=9.0),
        _c("r2", query_score=8.0),
        _c("q", query_score=1.0, quality={"quality_band": "highlight", "grade": "A"}),
        _c("u", query_score=0.9, quality={"quality_band": "uncertain"}),
        _c("z", query_score=0.5),
    ]
    picked = select_deep_set(cands, k=4)
    assert len(picked) == 4
    assert cands[0] in picked and cands[1] in picked  # 2 highest-relevance
    assert cands[2] in picked  # highest quality-adjusted pulled in
    assert len(set(id(c) for c in picked)) == 4  # no duplicates


# --- query plan + session round-trip ---------------------------------------

def test_query_plan_is_per_source():
    intent = SearchIntent(
        raw_query="agents in oncology",
        canonical_question="How are LLM agents used for oncology decision support?",
        concepts=["LLM agent", "oncology"],
    )
    plan = build_query_plan(intent)
    assert plan.arxiv and plan.europepmc  # every external channel gets a string
    assert plan.openalex_semantic == intent.canonical_question
    assert isinstance(plan.display(), list) and plan.display()


def test_session_round_trip_preserves_candidates_and_review():
    sess = ResearchSession(
        id="abc123", created_at="2026-07-10T00:00:00Z", raw_query="q",
        intent=SearchIntent(raw_query="q"), plan=QueryPlan(arxiv="q"),
        questions=["what dataset?"],
        candidates=[_c("P", doi="10.1/x", query_score=2.0, review={"state": "reviewed", "brief": {"tldr": "t"}})],
    )
    back = ResearchSession.from_dict(sess.to_dict())
    assert back.id == "abc123"
    assert back.candidates[0].doi == "10.1/x"
    assert back.candidates[0].review["brief"]["tldr"] == "t"
    assert back.questions == ["what dataset?"]


# --- intent parse (fake LLM) ------------------------------------------------

class _FakeLLM:
    def __init__(self, text):
        self._text = text

    def prompt(self, _prompt):
        return self._text


def test_parse_intent_uses_llm_json():
    llm = _FakeLLM('{"canonical_question": "How do X?", "concepts": ["x", "y"], "synonyms": ["z"]}')
    intent = parse_intent("x topic", ["what N?"], llm=llm)
    assert intent.canonical_question == "How do X?"
    assert intent.concepts == ["x", "y"]
    assert intent.questions == ["what N?"]
    assert intent.parse_ok is True


def test_parse_intent_handles_fenced_and_nested_json():
    # A small model's real failure mode (observed live): ```json fence, trailing
    # junk, and synonyms nested as a dict-of-lists. Must salvage, not fall back.
    text = (
        '```json\n{"canonical_question": "How X?", "concepts": ["a", "b"], '
        '"synonyms": {"a": ["a1", "a2"], "b": ["b1"]}, "study_types": []}\n```\nthanks!'
    )
    intent = parse_intent("x", [], llm=_FakeLLM(text))
    assert intent.canonical_question == "How X?"
    assert intent.parse_ok is True
    assert set(intent.synonyms) == {"a1", "a2", "b1"}  # dict-of-lists flattened


def test_parse_intent_falls_back_on_garbled(monkeypatch):
    intent = parse_intent("raw topic", [], llm=_FakeLLM("not json at all"))
    assert intent.canonical_question == "raw topic"  # spec §7 fallback
    assert intent.concepts == ["raw topic"]
    assert intent.parse_ok is False  # degraded plan is visible, never silent


# --- review adapters (heavy library calls stubbed) --------------------------

class _FakeQuality:
    def model_dump(self):
        return {"quality_band": "highlight", "grade": "A", "coverage_fraction": 0.8,
                "missing_critical": [], "paper_type": "empirical"}


def test_light_review_no_fulltext_is_uncertain():
    cand = _c("P")
    light_review(cand, full_text="", sections=[], llm=None, max_chars=1000)
    assert cand.quality["quality_band"] == "uncertain"
    assert cand.coverage["full_text_available"] is False


def test_light_review_happy_path(monkeypatch):
    monkeypatch.setattr(review_mod, "evaluate_quality", lambda **kw: _FakeQuality())
    cand = _c("P")
    light_review(cand, full_text="body text", sections=[{"title": "Methods"}], llm=object(), max_chars=1000)
    assert cand.quality["grade"] == "A"
    assert cand.coverage["full_text_available"] is True
    assert cand.coverage["sections_detected"] == ["Methods"]


class _FakeDigest:
    tldr = "the point"
    executive_summary = ""
    key_findings = ["f1"]
    read_decision = "read"
    read_why = ""
    methods = ""
    limitations = ""
    key_strength = ""
    key_weakness = ""
    grade = "B"


class _FakeSummary:
    def __init__(self, text, quotes):
        self.summary = text
        self.relevant = True
        self.retrieval_state = "hit"
        self.abstained = False
        self.supporting_quotes = quotes
        self.key_sections = ["Results"]


def test_targeted_review_no_fulltext():
    cand = _c("P")
    targeted_review(cand, full_text="", sections=[], query="q", questions=[], config=None, llm=None)
    assert cand.review == {"state": "needs_full_text"}


def test_targeted_review_happy_path(monkeypatch):
    monkeypatch.setattr(tr_mod, "assess_digest", lambda **kw: _FakeDigest())
    monkeypatch.setattr(
        tr_mod, "summarize_for_goals",
        lambda **kw: [_FakeSummary("lens", ["quoteL"]), _FakeSummary("ans1", ["quoteA"])],
    )
    cand = _c("P")
    targeted_review(
        cand, full_text="body", sections=[], query="my query", questions=["what dataset?"],
        config=object(), llm=object(),
    )
    assert cand.review["state"] == "reviewed"
    assert cand.review["brief"]["tldr"] == "the point"
    assert cand.review["query_lens"]["text"] == "lens"
    assert cand.review["question_answers"][0]["question"] == "what dataset?"
    assert cand.review["question_answers"][0]["supporting_quotes"] == ["quoteA"]


# --- PMC full-text XML → plain text (the OA full-text path) ------------------

def test_europepmc_xml_to_text_extracts_body_only():
    from zotero_summarizer.integrations.europepmc import _xml_to_text

    xml = (
        "<article><front><article-title>FRONTMATTER_ONLY</article-title></front>"
        "<body><sec><p>Body text here &amp; more.</p><p>Second para.</p></sec></body></article>"
    )
    text = _xml_to_text(xml)
    assert text == "Body text here & more. Second para."   # body-only, entities unescaped
    assert "FRONTMATTER_ONLY" not in text                   # journal metadata dropped
