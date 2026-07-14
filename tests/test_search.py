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
    # Same relevance bucket (within EPSILON=0.05): quality breaks the tie EVEN
    # against a slightly higher raw score. a has the higher query_score but no
    # quality; b is a hair lower but top quality → b wins the tie.
    a = _c("a", query_score=0.83, quality={"quality_band": "", "grade": "C"})
    b = _c("b", query_score=0.81, quality={"quality_band": "highlight", "grade": "A"})
    ordered = rank_candidates([a, b])
    assert ordered[0] is b


def test_quality_cannot_cross_bucket_in_unit_domain():
    # Regression: query_score is sigmoid [0,1]. A high-relevance/low-quality paper
    # must stay above a mid-relevance/top-quality one. With the OLD epsilon=0.5 both
    # fall in bucket floor(x/0.5) — 0.95→1, 0.50→1 — so quality would wrongly cross;
    # the retuned default (0.05) keeps them in distinct buckets (19 vs 10).
    hi = _c("hi", query_score=0.95, quality={"quality_band": "", "grade": "C"})
    lo = _c("lo", query_score=0.50, quality={"quality_band": "highlight", "grade": "A"})
    ordered = rank_candidates([lo, hi])
    assert ordered[0] is hi


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


# --- session store: single-flight claim + race-safe save_merge --------------

def _tmp_store(monkeypatch, tmp_path):
    from zotero_summarizer.services.search import session as session_mod

    class _S:
        search_dir = tmp_path

    monkeypatch.setattr(session_mod, "settings", lambda: _S())
    return session_mod


def test_claim_single_flights_the_worker(monkeypatch, tmp_path):
    store = _tmp_store(monkeypatch, tmp_path)
    sess = ResearchSession(
        id="s1", created_at="t", raw_query="q", intent=SearchIntent(raw_query="q"),
        plan=QueryPlan(), candidates=[], status="screened",
    )
    store.save(sess)
    assert store.claim("s1", expect="screened", to="reviewing") is True
    assert store.claim("s1", expect="screened", to="reviewing") is False  # already claimed
    assert store.load("s1").status == "reviewing"


def test_save_merge_preserves_concurrent_materialized_key(monkeypatch, tmp_path):
    # The review worker holds a stale copy (no key); a concurrent Add stamps the key;
    # the worker's whole-session save must NOT drop it (add-only/monotonic key).
    store = _tmp_store(monkeypatch, tmp_path)
    sess = ResearchSession(
        id="s2", created_at="t", raw_query="q", intent=SearchIntent(raw_query="q"),
        plan=QueryPlan(), candidates=[_c("P", doi="10.1/x")], status="reviewing",
    )
    store.save(sess)
    stale = store.load("s2")  # worker's in-memory copy, before the Add
    store.update("s2", lambda s: setattr(s.candidates[0], "materialized_zotero_key", "ZKEY1"))
    store.save_merge(stale)  # worker persists its stale copy
    assert store.load("s2").candidates[0].materialized_zotero_key == "ZKEY1"


def test_materialize_once_serializes_concurrent_adds(monkeypatch, tmp_path):
    # Two simultaneous "Add to library" clicks on the SAME candidate must write to
    # Zotero exactly once — the session lock serializes check→write→stamp. Without it
    # both threads would load "no key" before either wrote, and double-add.
    import threading
    import time

    store = _tmp_store(monkeypatch, tmp_path)
    cand = _c("P", doi="10.1/x")
    sess = ResearchSession(
        id="m4", created_at="t", raw_query="q", intent=SearchIntent(raw_query="q"),
        plan=QueryPlan(), candidates=[cand], status="screened",
    )
    store.save(sess)

    writes: list[str] = []

    def _do_write(c, s):
        time.sleep(0.05)  # widen the window so a missing lock reliably double-writes
        key = f"ZK{len(writes)}"
        writes.append(key)
        return key

    results: list[object] = []

    def _run():
        results.append(store.materialize_once("m4", cand.candidate_id, _do_write))

    threads = [threading.Thread(target=_run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(writes) == 1                                 # wrote exactly once
    assert sorted(r[0] for r in results) == [False, True]   # one added, one saw it done
    assert store.load("m4").candidates[0].materialized_zotero_key == "ZK0"


# --- materialize a candidate into the library (Add to library) --------------

def test_feed_payload_adapter_shape():
    # Correction #8: the writer wants authors as a LIST and publication_date (not year).
    from zotero_summarizer.services.search.materialize import _feed_payload

    c = _c("T", abstract="A", doi="10.1/x", authors=["Alice", "Bob"], year=2025, venue="Nature")
    p = _feed_payload(c)
    assert p["authors"] == ["Alice", "Bob"]      # list, not a comma string
    assert p["publication_date"] == "2025"        # year → publication_date
    assert p["publication_title"] == "Nature"
    assert "year" not in p
    assert p["item_type"] == "journalArticle"


def test_materialize_rejects_unknown_candidate(monkeypatch, tmp_path):
    from zotero_summarizer.api.errors import APIError
    from zotero_summarizer.services.search import materialize as mat

    _tmp_store(monkeypatch, tmp_path)
    sess = ResearchSession(
        id="m1", created_at="t", raw_query="q", intent=SearchIntent(raw_query="q"),
        plan=QueryPlan(), candidates=[_c("P", doi="10.1/x")], status="screened",
    )
    from zotero_summarizer.services.search import session as session_mod
    session_mod.save(sess)
    try:
        mat.materialize_candidate("m1", "does-not-exist", None)
        assert False, "expected APIError for unknown candidate"
    except APIError as exc:
        assert exc.status_code == 404


def test_materialize_is_idempotent(monkeypatch, tmp_path):
    # An already-added candidate returns its key without touching Zotero.
    from zotero_summarizer.services.search import materialize as mat

    _tmp_store(monkeypatch, tmp_path)
    cand = _c("P", doi="10.1/x", materialized_zotero_key="ZEXIST")
    sess = ResearchSession(
        id="m2", created_at="t", raw_query="q", intent=SearchIntent(raw_query="q"),
        plan=QueryPlan(), candidates=[cand], status="screened",
    )
    from zotero_summarizer.services.search import session as session_mod
    session_mod.save(sess)
    out = mat.materialize_candidate("m2", cand.candidate_id, None)
    assert out == {"status": "already_added", "zotero_key": "ZEXIST"}


def test_materialize_rejects_unknown_collection_key(monkeypatch, tmp_path):
    # Correction #3: a picker key the reader can't resolve is a 400, never a junk
    # auto-create — and it must fire BEFORE any Zotero write is attempted.
    from zotero_summarizer.api.errors import APIError
    from zotero_summarizer.services.search import materialize as mat

    _tmp_store(monkeypatch, tmp_path)

    class _Reader:
        def __init__(self, *a, **k):
            pass

        def collection_name_for_key(self, key):
            return None  # unknown key

    monkeypatch.setattr(mat, "ZoteroReader", _Reader)
    # A writer construction would blow up the test if we ever reached it — we shouldn't.
    monkeypatch.setattr(mat, "ZoteroWriter", lambda *a, **k: (_ for _ in ()).throw(AssertionError("wrote despite bad key")))

    sess = ResearchSession(
        id="m3", created_at="t", raw_query="q", intent=SearchIntent(raw_query="q"),
        plan=QueryPlan(), candidates=[_c("P", doi="10.1/x")], status="screened",
    )
    from zotero_summarizer.services.search import session as session_mod
    session_mod.save(sess)
    cand_id = sess.candidates[0].candidate_id
    try:
        mat.materialize_candidate("m3", cand_id, "BOGUSKEY")
        assert False, "expected APIError for unknown collection key"
    except APIError as exc:
        assert exc.status_code == 400


# --- relevance band + why chips (Phase C) -----------------------------------

def test_relevance_bands_tercile_split():
    from zotero_summarizer.services.search._relevance import band_for, relevance_bands

    cands = [_c(f"P{i}", query_score=s) for i, s in enumerate([0.9, 0.8, 0.6, 0.4, 0.2, 0.05])]
    strong, mild = relevance_bands(cands)
    assert strong is not None and mild is not None and strong > mild
    assert band_for(0.9, strong, mild) == "strong"
    assert band_for(0.01, strong, mild) == "weak"


def test_relevance_bands_absolute_floor_kills_all_bad_pool():
    # A pool of all-tiny scores must NOT crown its least-bad third "strong".
    from zotero_summarizer.services.search._relevance import band_for, relevance_bands

    cands = [_c(f"P{i}", query_score=s) for i, s in enumerate([0.04, 0.03, 0.02, 0.01])]
    strong, mild = relevance_bands(cands)
    assert all(band_for(c.query_score, strong, mild) != "strong" for c in cands)


def test_relevance_bands_suppressed_below_three():
    from zotero_summarizer.services.search._relevance import relevance_bands

    assert relevance_bands([_c("A", query_score=0.9), _c("B", query_score=0.5)]) == (None, None)


def test_build_why_chips_ordered_and_capped():
    from zotero_summarizer.services.search._models import Provenance
    from zotero_summarizer.services.search._relevance import build_why

    c = _c(
        "P", version_type="version_of_record",
        provenance=[Provenance(source="arxiv", query_variant="q"),
                    Provenance(source="openalex", query_variant="q")],
    )
    c.quality = {"grade": "A", "quality_band": "highlight"}
    why = build_why(c, "strong")
    assert why[0] == "Strong match"        # relevance leads
    assert "In 2 sources" in why           # cross-source agreement
    assert "Grade A" in why                # graded quality
    assert len(why) <= 3                    # capped (Peer-reviewed drops off)


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
