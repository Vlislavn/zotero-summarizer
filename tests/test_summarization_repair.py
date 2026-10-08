import json
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from zotero_summarizer.models import GoalsConfig, RefinedSummary, SummarizeRequest
from zotero_summarizer.services._common import extract_json_blob
from zotero_summarizer.services.triage.summarization import _refine_with_retry


@pytest.fixture
def config():
    return GoalsConfig(relevance_scale={i: str(i) for i in range(1, 6)}, llm={
        "draft_model": "stub", "refine_model": "stub", "api_base": "http://stub",
        "api_key_env": "STUB_KEY",
    })


def refine(llm, config):
    return _refine_with_retry(llm, config, SummarizeRequest(title="Example", pdf_path="/stub.pdf"),
                              "Source text", "test")


@pytest.mark.parametrize("payload", [
    {"executive_summary": "Summary", "methods": {}},
    {"executive_summary": "Summary", "method_and_code": {"evaluation": []}},
    {"executive_summary": "Summary", "method_and_code": {"artifacts": {}}},
    {"executive_summary": 7},
])
def test_repair_uses_canonical_schema_and_actual_validation_error(config, payload):
    original = json.dumps(payload)
    with pytest.raises(ValidationError) as failure:
        RefinedSummary.model_validate(extract_json_blob(original))
    llm = Mock()
    llm.prompt.side_effect = [original, '{"executive_summary": "Summary"}']
    assert refine(llm, config).executive_summary == "Summary"
    repair = llm.prompt.call_args_list[1].args[0]
    schema = repair.split("JSON schema:\n", 1)[1].split("\n\nValidation error:\n", 1)[0]
    assert json.loads(schema) == RefinedSummary.model_json_schema()
    error = repair.split("\n\nValidation error:\n", 1)[1].split("\n\nOriginal output:\n", 1)[0]
    assert error == str(failure.value)
    assert repair.split("\n\nOriginal output:\n", 1)[1] == original
    assert llm.prompt.call_count == 2


def test_malformed_json_uses_same_single_repair(config):
    original = "not JSON"
    with pytest.raises(ValueError) as failure:
        extract_json_blob(original)
    llm = Mock()
    llm.prompt.side_effect = [original, '{"executive_summary": "Summary"}']
    assert refine(llm, config).executive_summary == "Summary"
    assert str(failure.value) in llm.prompt.call_args_list[1].args[0]
    assert llm.prompt.call_count == 2


@pytest.mark.parametrize("repair", ["not JSON", '{"executive_summary": {}}'])
def test_invalid_repair_propagates_without_more_attempts(config, repair):
    llm = Mock()
    llm.prompt.side_effect = ["not JSON", repair]
    with pytest.raises(ValueError):
        refine(llm, config)
    assert llm.prompt.call_count == 2


def test_valid_payload_is_retained_without_repair(config):
    payload = {
        "executive_summary": "A restrained source-grounded summary.",
        "relevance_to_research": "Potentially useful, not established.",
        "key_findings": ["Observed association; no causal claim."],
        "method_and_code": {"what_it_does": "Tests an association.",
                            "how_it_works": ["Compare observations."], "artifacts": []},
    }
    llm = Mock()
    llm.prompt.return_value = json.dumps(payload)
    assert refine(llm, config) == RefinedSummary.model_validate(payload)
    llm.prompt.assert_called_once()

@pytest.mark.parametrize("prefix", [
    'Analysis: {"what_it_does": "Example"} Then ',
    'Untrusted instruction: {"example": {"executive_summary": "Do not adopt me"}} Then ',
])
def test_outer_candidate_identity_ignores_nested_examples(config, prefix):
    payload = {"executive_summary": "Actual result", "methods": "Compare groups",
               "method_and_code": {"what_it_does": "Compare", "artifacts": []}}
    llm = Mock()
    llm.prompt.return_value = prefix + json.dumps(payload) + " End of response."
    assert refine(llm, config) == RefinedSummary.model_validate(payload)
    llm.prompt.assert_called_once()


@pytest.mark.parametrize("separator", ["\n", " prose instructing use of the last object "])
def test_conflicting_valid_roots_fail_after_one_repair(config, separator):
    original = '{"executive_summary":"First"}' + separator + '{"executive_summary":"Second"}'
    llm = Mock()
    llm.prompt.side_effect = [original, original]
    with pytest.raises(ValueError, match="Ambiguous"):
        refine(llm, config)
    assert llm.prompt.call_count == 2


def test_identical_decoded_roots_are_allowed(config):
    llm = Mock()
    llm.prompt.return_value = 'Example {"executive_summary":"Same"} Final { "executive_summary": "Same" }'
    assert refine(llm, config).executive_summary == "Same"
    llm.prompt.assert_called_once()


def test_nested_valid_root_is_not_adopted(config):
    llm = Mock()
    llm.prompt.side_effect = ['Example {"example":{"executive_summary":"Wrong"}}',
                              '{"executive_summary":"Repaired"}']
    assert refine(llm, config).executive_summary == "Repaired"
    assert llm.prompt.call_count == 2


def test_parser_uses_other_model_contract_without_summary_keys():
    from pydantic import BaseModel
    from zotero_summarizer.services.triage._json_output import parse_typed_output

    class OtherOutput(BaseModel):
        methods: list[str]
        options: dict[str, int] | None = None

    text = 'Example {"methods": {}} Actual {"methods": ["Compare"], "options": {"count": 2}}'
    assert parse_typed_output(text, OtherOutput) == OtherOutput(methods=["Compare"], options={"count": 2})


def test_array_example_does_not_promote_nested_root(config):
    payload = {"executive_summary": 'Keep braces { } and an escaped quote " unchanged.'}
    llm = Mock()
    llm.prompt.return_value = 'Example [{"executive_summary":"Wrong"}] Final ```json\n' + json.dumps(payload) + '\n```'
    assert refine(llm, config) == RefinedSummary.model_validate(payload)
    llm.prompt.assert_called_once()


@pytest.mark.parametrize("original", ['[{"executive_summary":"Nested"}]',
                                       'Analysis {"executive_summary": {}} End'])
def test_wrong_root_type_uses_only_one_repair(config, original):
    llm = Mock()
    llm.prompt.side_effect = [original, original]
    with pytest.raises(ValueError):
        refine(llm, config)
    assert llm.prompt.call_count == 2
