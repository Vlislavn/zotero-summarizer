"""A181: every rendered factual digest field reaches claim decomposition."""

import json
from unittest.mock import Mock

from zotero_summarizer.models import PaperDigest
from zotero_summarizer.services.faithbench._build_claims import CLAIM_FIELDS, decompose_digest, snippets_from_digest


def test_claim_field_policy_covers_entire_digest_schema():
    judgments_and_metadata = {
        "grade", "soundness", "novelty", "significance", "reproducibility", "clarity",
        "confidence", "basis", "read_decision", "estimated_read_minutes", "writing_friction",
    }
    assert set(CLAIM_FIELDS).isdisjoint(judgments_and_metadata)
    assert set(CLAIM_FIELDS) | judgments_and_metadata == set(PaperDigest.model_fields)


def test_nested_parameters_preserve_false_and_all_technical_values():
    snippets = snippets_from_digest({"parameters": {
        "dataset": "ImageNet", "baselines": ["ResNet"], "sample_size": "123",
        "metrics": ["85.3%"], "architecture": "GlassNet", "external_validation": False,
    }})
    assert json.loads(snippets["parameters"])["external_validation"] is False
    assert all(token in snippets["parameters"] for token in ("ImageNet", "ResNet", "123", "85.3%", "GlassNet"))


def test_new_fields_reach_decomposer_instead_of_old_six_field_cache(tmp_path):
    (tmp_path / "claims-v2-abc.json").write_text(json.dumps([{"field": "tldr", "claim": "Old coverage"}]))
    digest = {"executive_summary": "False SecretSet accuracy is 99%.", "methods": "Used GlassNet.",
              "limitations": "Only ten subjects.", "key_findings": ["Result."],
              "industry_impact": "Impact.", "academy_impact": "Scientific impact.",
              "unknown_unknowns": "Uncertainty.", "parameters": {"dataset": "SecretSet"}}
    llm = Mock()
    llm.prompt.return_value = json.dumps({"claims": [{"field": field, "claim": "Factual claim."} for field in digest]})

    rows = decompose_digest(digest_dump=digest, digest_sha="abc", title="Paper", decompose_llm=llm, cache_dir=tmp_path)

    prompt = llm.prompt.call_args.args[0]
    assert all(f"[{field}]" in prompt for field in digest)
    assert "99%" in prompt and "SecretSet" in prompt
    assert {row["field"] for row in rows} == set(digest)
    assert (tmp_path / "claims-v2-abc.json").exists()
