"""Code availability is not a reproduction-duration measurement."""
import pytest

from test_verification_audit_export import KEY, audit_env, publish  # noqa: F401
from zotero_summarizer.models import (
    ResearchCandidate, ResearchEngineeringCard, ResearchFeedTriage, ResearchProfile,
)
from zotero_summarizer.services.research_feed.card import _reproducibility, build_card


CODE = "https://example.test/code"


@pytest.mark.parametrize("urls,gaps,tier,missing", [
    ([CODE], [], "unknown", []),
    ([], [], "blocked", ["No verified code URL"]),
    ([CODE], ["Dataset missing", "Dataset missing"], "blocked", ["Dataset missing"]),
    ([CODE], ["", " "], "unknown", []),
])
def test_code_and_reported_gaps_do_not_supply_a_duration(urls, gaps, tier, missing):
    assert _reproducibility({"quality": {"missing_critical": gaps}}, urls) == (tier, missing)


def test_card_serializes_unknown_effort_without_losing_artifact_metadata(audit_env):
    review, _, _ = publish(audit_env)
    review["code_link"] = {
        "found": True, "exists": True, "relevance": "matched", "url": CODE,
    }
    source = ResearchCandidate(source_id=KEY, source="rss", title="Methods")
    triage = ResearchFeedTriage(include=True, score=5, confidence=1, rationale="Fixture")
    profile = ResearchProfile(themes=["methods"], projects=["project"], topic_taxonomy=[])

    result = build_card(source, triage, review, profile)

    assert result.reproducibility_tier == "unknown"
    assert result.reproducibility_rationale == "Reproduction effort has not been assessed."
    assert result.missing_reproduction_inputs == []
    assert result.code_urls == [review["code_link"]["url"]]
    assert result.source_id == source.source_id
    restored = ResearchEngineeringCard.model_validate_json(result.model_dump_json())
    assert restored.reproducibility_tier == "unknown" and restored.code_urls == result.code_urls


def test_card_keeps_existing_blockers_and_their_order(audit_env, monkeypatch):
    from zotero_summarizer.services.library import deep_review

    quality = {"missing_critical": ["Dataset missing", "Dataset missing"]}
    monkeypatch.setattr(deep_review._deep_review_layers, 'extra_layers',
                        lambda _ctx: (quality, None, None, None, None))
    review, _, _ = publish(audit_env)
    source = ResearchCandidate(source_id=KEY, source="rss", title="Methods")
    triage = ResearchFeedTriage(include=True, score=5, confidence=1, rationale="Fixture")
    profile = ResearchProfile(themes=["methods"], projects=["project"], topic_taxonomy=[])

    result = build_card(source, triage, review, profile)

    assert result.reproducibility_tier == "blocked"
    assert result.missing_reproduction_inputs == ["Dataset missing", "No verified code URL"]
    assert result.reproducibility_rationale == "Dataset missing; No verified code URL"
    assert result.code_urls == []
