from zotero_summarizer.services.search._models import Candidate, SearchIntent, QueryPlan
from zotero_summarizer.services.search.intent import parse_intent, build_query_plan
from zotero_summarizer.services.search.refine import _delta_intent
from zotero_summarizer.services.search.federate import _matches_constraints


class Planner:
    def prompt(self, prompt):
        return '{"canonical_question":"Compare verification and validation", "concepts":["verification","validation"],"must_include":["verification","validation"],"must_not_include":["therapy"],"study_types":["review"]}'


def test_model_restrictions_are_proposals():
    intent = parse_intent('Compare verification and validation', [], llm=Planner())
    plan = build_query_plan(intent)
    assert plan.must_include == []
    assert plan.must_not_include == []
    assert plan.study_types == []
    assert plan.pending_constraints['must_not_include'] == ['therapy']
    assert _matches_constraints(Candidate(title='Design validation'), plan)
    assert 'Required terms' not in plan.openalex_semantic


def test_refinement_cannot_promote_model_drops():
    base = SearchIntent(raw_query='topic', must_not_include=['animal'])
    delta = _delta_intent(base, ['new facet'])
    assert delta.must_not_include == ['animal']


def test_confirmed_types_require_metadata():
    plan = QueryPlan(study_types=['review'], constraint_origin='user_confirmed')
    assert not _matches_constraints(Candidate(title='A review'), plan)
    assert _matches_constraints(Candidate(title='Methods', publication_types=['review']), plan)


def test_old_saved_plan_remains_legacy():
    plan = QueryPlan.from_dict({'must_include':['verification','validation']})
    assert plan.constraint_origin == 'legacy_unknown'
    assert not _matches_constraints(Candidate(title='Design validation'), plan)


def test_proposals_and_authority_survive_roundtrip():
    plan = build_query_plan(parse_intent('topic', [], llm=Planner()))
    restored = QueryPlan.from_dict(plan.to_dict())
    assert restored.constraint_origin == 'model_proposed'
    assert restored.pending_constraints == plan.pending_constraints
    assert restored.must_include == []


def test_accounting_counts_cross_source_families():
    from zotero_summarizer.services.search._models import Provenance
    from zotero_summarizer.services.search._accounting import account_retrieval
    observations = [Candidate(title='Review', doi='10.1/a', provenance=[Provenance('a', 'q')]),
                    Candidate(title='Review', doi='10.1/a', provenance=[Provenance('b', 'q')])]
    family = Candidate(title='Review', doi='10.1/a', provenance=[Provenance('a', 'q'), Provenance('b', 'q')])
    from collections import Counter
    raw = Counter(p.source for candidate in observations for p in candidate.provenance)
    counts = account_retrieval(raw, [family], [], QueryPlan(study_types=['review'], constraint_origin='user_confirmed'), [])
    assert counts['a']['unknown_type_rejected'] == 1
    assert counts['b']['families'] == 1
    assert counts['a']['accepted'] == 0
