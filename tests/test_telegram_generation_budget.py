"""Machine output-work limits do not narrow retained Telegram report history."""
from copy import deepcopy
import json

import pytest

from tests.test_telegram_conversations import snapshot, response, generated_response
from tests.test_research_feed_telegram import _project
from zotero_summarizer.models import GoalsConfig
from zotero_summarizer.models.providers import ProviderConfig
from zotero_summarizer.models.research_feed import TelegramLimits, TelegramModelSelection
from zotero_summarizer.models.telegram_generation import generation_schema, expand_topics
from zotero_summarizer.services.research_feed import telegram_review
from zotero_summarizer.services.research_feed.profile import load_profile

ROLES = {"e1": "discussion", "e2": "news"}
EVIDENCE = {
    "e1": {"source_id": "telegram:-1000000000002:51", "role": "discussion", "quote": "participant claim"},
    "e2": {"source_id": "telegram:-1000000000001:7", "role": "news", "quote": "Author news"},
}


def test_generation_schema_bounds_work_without_repeating_canonical_membership():
    schema = generation_schema(4096, ROLES).model_json_schema()
    assert schema["properties"]["topics"]["maxItems"] == 4
    assert generation_schema(2048, ROLES).model_json_schema()["properties"]["topics"]["maxItems"] == 2
    definitions = schema["$defs"]
    assert "source_ids" not in definitions["TelegramMiniTopic"]["properties"]
    assert set(definitions["TelegramMiniCitation"]["properties"]) == {"evidence_id"}
    assert definitions["TelegramMiniAssertion"]["properties"]["text"]["maxLength"] == 320
    assert definitions["TelegramMiniAssertion"]["properties"]["citations"]["maxItems"] == 2
    assert all(definitions["TelegramMiniTopic"]["properties"][name]["maxItems"] == 1
               for name in ("news", "discussion", "contrasts", "evidence", "caveats", "implications"))


def test_compact_generation_derives_exact_quotes_and_membership_with_independent_validation():
    canonical = expand_topics(generated_response(), generation_schema(4096, ROLES), EVIDENCE)
    assert canonical["topics"][0]["source_ids"] == ["telegram:-1000000000002:51"]
    retained = telegram_review.validate_topics(canonical, [snapshot()])
    assert retained.topics[0].discussion[0].citations[0].quote == "participant claim"


def test_compact_generation_does_not_narrow_previously_valid_retained_topics():
    text = "An exact original retained statement. " * 20
    old = response()
    claim = {"text": "Retained analysis. " * 70,
             "citations": [{"source_id": "telegram:-1000000000002:51", "quote": text[:400]}]}
    for name in ("context", "discussion", "caveats"):
        old["topics"][0][name] = claim if name == "context" else [claim]
    retained = telegram_review.validate_topics(old, [snapshot(text)])
    assert len(retained.topics[0].context.text) > 320
    assert len(retained.topics[0].context.citations[0].quote) == 400


def test_generation_passes_compact_schema_once_and_retains_canonical_output(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from zotero_summarizer.services.llm import factory

    settings = _project(tmp_path)
    config = GoalsConfig.model_validate(json.loads(settings.config_path.read_text()))
    config.llm_routing.providers.append(ProviderConfig(
        name="local", base_url="http://127.0.0.1:11434/v1", api_key_env="LOCAL"))
    profile = load_profile(settings.data_dir)
    profile.telegram_model = TelegramModelSelection(provider="local", model="synthetic")
    profile.telegram_limits = TelegramLimits(context_tokens=32768, output_tokens=4096)
    client = Mock(); client.pydantic_prompt.return_value = generated_response()
    monkeypatch.setattr(factory, "build_client_for_provider", Mock(return_value=client))
    value, identity = telegram_review.generate(config, profile, [snapshot()])
    assert value.topics[0].source_ids == ["telegram:-1000000000002:51"]
    assert value.topics[0].context.citations[0].quote == "A participant claim"
    assert client.pydantic_prompt.call_count == 1
    passed_schema = client.pydantic_prompt.call_args.args[1].model_json_schema()
    assert passed_schema["properties"]["topics"]["maxItems"] == 4
    assert identity["schema_sha256"] == telegram_review.digest(passed_schema)
    assert identity["evidence_map_sha256"] == telegram_review.digest(
        telegram_review._evidence_catalog(telegram_review.admitted_sources([snapshot()])))
    assert client.pydantic_prompt.call_args.kwargs == {"attempt_fix": False, "truncate_prompt": False}
    client.close.assert_called_once()


def test_generated_citations_are_scoped_to_actual_roles_without_reclassification():
    schema = generation_schema(4096, ROLES)
    compact = generated_response()
    assert expand_topics(compact, schema, EVIDENCE)["topics"][0]["discussion"]
    wrong = deepcopy(compact)
    wrong["topics"][0]["discussion"][0]["citations"][0]["evidence_id"] = "e2"
    with pytest.raises(ValueError):
        expand_topics(wrong, schema, EVIDENCE)
    news = deepcopy(wrong)
    news["topics"][0]["news"] = news["topics"][0]["discussion"]
    news["topics"][0]["discussion"] = []
    assert expand_topics(news, schema, EVIDENCE)["topics"][0]["news"]
    only_discussion = generation_schema(4096, {"e1": "discussion"})
    with pytest.raises(ValueError):
        expand_topics(news, only_discussion, EVIDENCE)
    assert only_discussion.model_json_schema()["$defs"]["TelegramMiniTopic"]["properties"]["news"]["maxItems"] == 0


def test_projected_input_omits_only_empty_url_metadata_and_uses_compact_json(tmp_path):
    from zotero_summarizer.models.providers import LLMRoutingConfig, DefaultModelConfig
    from zotero_summarizer.services.library._prompt_security import untrusted_input

    batches = [snapshot()]
    sources = telegram_review.admitted_sources(batches)
    evidence = telegram_review._evidence_catalog(sources)
    rows = telegram_review._project_sources(batches, sources, evidence)
    assert "urls" not in rows[0]
    sources[next(iter(sources))]["original"]["urls"] = ["https://example.invalid/hidden-link"]
    assert telegram_review._project_sources(batches, sources, evidence)[0]["urls"] == ["https://example.invalid/hidden-link"]
    settings = _project(tmp_path)
    config = GoalsConfig.model_validate(json.loads(settings.config_path.read_text()))
    config.llm_routing = LLMRoutingConfig(providers=[ProviderConfig(
        name="local", base_url="http://127.0.0.1:11434/v1", api_key_env="LOCAL")],
        default=DefaultModelConfig(provider="local", model="synthetic"))
    profile = load_profile(settings.data_dir)
    profile.telegram_model = TelegramModelSelection(provider="local", model="synthetic")
    profile.telegram_limits = TelegramLimits(context_tokens=32768)
    _, prompt, _ = telegram_review.prepare(config, profile, batches)
    assert prompt.endswith(untrusted_input(json.dumps(rows, ensure_ascii=False, separators=(",", ":"))))


def test_generation_reuses_exact_spans_without_free_quote_copying_or_content_loss():
    batches = [snapshot("A participant claim. Повтор. " * 40)]
    sources = telegram_review.admitted_sources(batches)
    catalog = telegram_review._evidence_catalog(sources)
    assert len(catalog) > 1
    assert all(item["quote"] == sources[item["source_id"]]["original"]["text"]
               [item["start"]:item["end"]] for item in catalog.values())
    assert all(len(item["quote"]) <= telegram_review.EVIDENCE_CHARACTERS for item in catalog.values())
    assert "".join("".join(item["quote"].split()) for item in catalog.values()) == "".join(batches[0].messages[0].text.split())
    schema = generation_schema(4096, {key: item["role"] for key, item in catalog.items()})
    compact = generated_response(next(iter(catalog)))
    retained = telegram_review.validate_topics(expand_topics(compact, schema, catalog), batches)
    assert retained.topics[0].context.citations[0].quote == catalog[next(iter(catalog))]["quote"]
    props = schema.model_json_schema()["$defs"]["TelegramMiniCitation"]["properties"]
    assert set(props) == {"evidence_id"}
