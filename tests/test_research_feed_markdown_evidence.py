"""Machine-field visibility and preservation, not scientific or human-use grading."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.test_research_feed import _review, _seed, _settings
from zotero_summarizer.models import (
    ResearchCandidate,
    ResearchEngineeringCard,
    ResearchFeedTriage,
)
from zotero_summarizer.services.research_feed import render
from zotero_summarizer.services.research_feed import runner as research_runner
from zotero_summarizer.services.research_feed.runner import run_weekly
from zotero_summarizer.storage import repositories


FIELDS = (
    ("reproducibility_tier", "Reproducibility tier"),
    ("reproducibility_rationale", "Reproducibility rationale"),
    ("missing_reproduction_inputs", "Missing reproduction inputs"),
    ("evidence_gaps", "Evidence gaps"),
)
UNRECORDED = "not recorded (not assessed)"


def _record(key, tier):
    # Explicit typed display fixtures: these are not generated scientific assessments.
    candidate = ResearchCandidate(
        source_id=key, source="rss", title=f"研究 {key} · **verbatim**",
    )
    triage = ResearchFeedTriage(
        include=True, score=3, confidence=0.5, rationale="Fixture only",
        matched_projects=["agent harness", "биомедицина"],
    )
    card = ResearchEngineeringCard(
        source_id=key, problem=f"Проблема {key}", core_idea=f"核心 {key}",
        engineering_novelty="Fixture only", worth_reading="skim",
        research_impact=0, production_impact=0, personal_novelty=0,
        project_use_notes=[f"harness: идея {key}", f"biomed: идея {key}"],
        code_urls=[f"https://example.test/{key}/code"],
        dataset_urls=[f"https://example.test/{key}/data"],
        model_urls=[f"https://example.test/{key}/model"],
        reproducibility_tier=tier,
        reproducibility_rationale=f"Журнал {key}: **raw Markdown**\n" + "λ " * 300,
        missing_reproduction_inputs=[
            f"入力 {key}: checkpoint α", f"seed {key}: café / cafe\u0301",
            f"入力 {key}: checkpoint α",
        ],
        evidence_gaps=[
            f"科学 {key}: 未検証\nвторая строка", f"Control {key}: 未比較",
            f"科学 {key}: 未検証\nвторая строка",
        ],
    )
    return {
        "candidate": candidate.model_dump(mode="json"),
        "triage": triage.model_dump(mode="json"),
        "card": card.model_dump(mode="json"),
    }


@pytest.fixture
def typed_payload():
    return {
        "metadata": {
            "from": "2026-08-20", "to": "2026-08-29",
            "counts": {"discovered": 4, "shortlisted": 3, "cards_generated": 2},
        },
        "cards": [_record("A", "blocked"), _record("B", "one_week")],
        "rejected_near_threshold": [{
            "candidate": {"title": "Отклонено · 全文"},
            "triage": {"rationale": "Полная причина\nбез сокращения"},
        }],
        "manual_full_text_required": [{"title": "Нужен полный текст · 手動"}],
    }


@pytest.mark.parametrize("has_code", [False, True])
@pytest.mark.parametrize("scientific_gap", [False, True])
def test_weekly_persisted_markdown_exposes_actual_card_uncertainty(
    tmp_path, monkeypatch, has_code, scientific_gap,
):
    settings = _settings(tmp_path)
    _seed(settings, "visible-owned", "Agent evaluation method", materialized="Z1")
    gap = "No held-out comparison is reported"
    review = _review("visible-owned")
    if not has_code:
        review["code_link"] = {
            "found": False, "exists": False, "relevance": "missing",
        }
    review["quality"]["missing_critical"] = [gap] if scientific_gap else []
    original_review = deepcopy(review)
    loader = Mock(return_value=review)
    queue_tags = Mock(side_effect=AssertionError("dry-run must not queue tags"))
    monkeypatch.setattr(research_runner, "_queue_tags", queue_tags)
    submitted = []

    def persist_owned(payload, output_dir, slug):
        before = deepcopy(payload)
        paths = render.persist(payload, output_dir, slug)
        assert payload == before
        submitted.append(before)
        return paths

    monkeypatch.setattr(research_runner, "persist", persist_owned)
    result = run_weekly(
        settings, start=datetime(2026, 8, 20, tzinfo=timezone.utc),
        end=datetime(2026, 8, 29, tzinfo=timezone.utc), dry_run=True,
        queue_zotero=True, review_loader=loader,
    )
    json_path, md_path = Path(result["json_path"]), Path(result["markdown_path"])
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    text = md_path.read_text(encoding="utf-8")
    assert payload == submitted[0]
    assert len(submitted) == result["cards_generated"] == len(payload["cards"]) == 1
    card = payload["cards"][0]["card"]
    assert card["reproducibility_tier"] == "unknown"
    assert card["reproducibility_rationale"] == (
        "Reproduction effort and required inputs have not been assessed."
    )
    assert card["missing_reproduction_inputs"] == []
    assert card["evidence_gaps"] == ([gap] if scientific_gap else [])
    assert "**Reproducibility tier:** unknown" in text.splitlines()
    assert (
        f"**Reproducibility rationale:** {card['reproducibility_rationale']}"
    ) in text.splitlines()
    assert f"**Missing reproduction inputs:** {UNRECORDED}" in text.splitlines()
    assert (
        f"**Evidence gaps:** {gap if scientific_gap else UNRECORDED}"
    ) in text.splitlines()
    assert card["code_urls"] == (
        ["https://github.com/example/visible-owned"] if has_code else []
    )
    assert card["worth_reading"] == "skim"
    assert json_path.parent == md_path.parent
    assert json_path.parent.parent == settings.data_dir / "research_feed"
    state = json.loads((json_path.parent.parent / "state.json").read_text())
    assert state["last_json"] == str(json_path)
    assert payload["metadata"]["dry_run"] is True
    assert result["writebacks_queued"] == 0
    queue_tags.assert_not_called()
    loader.assert_called_once_with("visible-owned")
    assert review == original_review
    with repositories.with_db_path(settings.triage_db_path):
        assert repositories.get_pending_changes(status=None) == []


@pytest.mark.parametrize(
    ("field", "label", "expected"),
    [
        ("reproducibility_tier", "Reproducibility tier", "blocked"),
        (
            "reproducibility_rationale", "Reproducibility rationale",
            "Журнал A: **raw Markdown**\n" + "λ " * 300,
        ),
        (
            "missing_reproduction_inputs", "Missing reproduction inputs",
            "入力 A: checkpoint α; seed A: café / cafe\u0301; 入力 A: checkpoint α",
        ),
        (
            "evidence_gaps", "Evidence gaps",
            "科学 A: 未検証\nвторая строка; Control A: 未比較; "
            "科学 A: 未検証\nвторая строка",
        ),
    ],
)
def test_each_field_keeps_full_unicode_order_and_duplicates(
    typed_payload, field, label, expected,
):
    before = deepcopy(typed_payload)
    text = render.markdown(typed_payload)
    first_card = text.split("## 2. ", 1)[0]
    assert f"**{label}:** {expected}\n" in first_card
    assert typed_payload == before
    if field == "missing_reproduction_inputs":
        assert expected not in first_card.split("**Evidence gaps:**", 1)[1]
    if field == "evidence_gaps":
        inputs_line = first_card.split("**Missing reproduction inputs:** ", 1)[1]
        assert expected not in inputs_line.split("\n\n", 1)[0]


@pytest.mark.parametrize("tier", ["unknown", "blocked", "one_day", "one_week", "multi_week"])
def test_existing_tier_is_projected_without_reassessment(typed_payload, tier):
    typed_payload["cards"] = [_record("A", tier)]
    text = render.markdown(typed_payload)
    assert f"**Reproducibility tier:** {tier}" in text.splitlines()
    assert typed_payload["cards"][0]["card"]["reproducibility_tier"] == tier


@pytest.mark.parametrize(("field", "label"), FIELDS)
@pytest.mark.parametrize("state", ["missing", "empty", "null"])
def test_optional_legacy_field_states_are_honest(typed_payload, field, label, state):
    card = typed_payload["cards"][0]["card"]
    if state == "missing":
        del card[field]
    elif state == "empty":
        card[field] = [] if field in {"missing_reproduction_inputs", "evidence_gaps"} else ""
    else:
        card[field] = None
    before = deepcopy(typed_payload)
    text = render.markdown(typed_payload).split("## 2. ", 1)[0]
    assert f"**{label}:** {UNRECORDED}" in text.splitlines()
    assert typed_payload == before


def test_minimal_legacy_card_keeps_existing_content_and_sections(typed_payload):
    for record in typed_payload["cards"]:
        record["candidate"] = {"title": record["candidate"]["title"]}
        record["triage"] = {"matched_projects": record["triage"]["matched_projects"]}
        record["card"] = {
            field: record["card"][field] for field in (
                "worth_reading", "problem", "core_idea", "project_use_notes",
                "code_urls", "dataset_urls", "model_urls",
            )
        }
    text = render.markdown(typed_payload)
    assert text.startswith(
        "# Research Intelligence · 2026-08-20 → 2026-08-29\n\n"
        "Discovered 4; shortlisted 3; cards 2.\n\n"
    )
    for index, key in enumerate(("A", "B"), 1):
        assert (
            f"## {index}. 研究 {key} · **verbatim**\n\n"
            "**Action:** skim · **Projects:** agent harness, биомедицина\n\n"
            f"Проблема {key}\n\n**Core idea:** 核心 {key}\n\n"
            f"**Project use:** harness: идея {key}; biomed: идея {key}\n\n"
            f"**Artifacts:** https://example.test/{key}/code, "
            f"https://example.test/{key}/data, https://example.test/{key}/model\n"
        ) in text
    for _field, label in FIELDS:
        assert text.count(f"**{label}:** {UNRECORDED}\n") == 2
    assert text.index("## 1. 研究 A") < text.index("## 2. 研究 B")
    assert text.endswith(
        "## Near-threshold rejects\n\n"
        "- Отклонено · 全文 — Полная причина\nбез сокращения\n\n"
        "## Needs full text\n\n- Нужен полный текст · 手動\n"
    )
    del typed_payload["rejected_near_threshold"]
    del typed_payload["manual_full_text_required"]
    minimal_text = render.markdown(typed_payload)
    assert "## Near-threshold rejects" not in minimal_text
    assert "## Needs full text" not in minimal_text
    typed_payload["cards"] = []
    assert render.markdown(typed_payload).endswith("Discovered 4; shortlisted 3; cards 2.\n")


def test_multiple_cards_keep_qualifications_in_their_own_sections(typed_payload):
    text = render.markdown(typed_payload)
    first, second = text.split("## 2. 研究 B", 1)
    second = second.split("## Near-threshold rejects", 1)[0]
    for section, key, tier in ((first, "A", "blocked"), (second, "B", "one_week")):
        assert f"**Reproducibility tier:** {tier}" in section.splitlines()
        assert f"**Reproducibility rationale:** Журнал {key}:" in section
        assert f"**Missing reproduction inputs:** 入力 {key}:" in section
        assert f"**Evidence gaps:** 科学 {key}:" in section
    assert "Журнал B:" not in first and "Журнал A:" not in second
    labels = [text.index(f"**{label}:**") for _field, label in FIELDS]
    assert labels == sorted(labels)


def test_rendering_preserves_canonical_json_inputs_and_prior_publication(
    tmp_path, typed_payload,
):
    before = deepcopy(typed_payload)
    inputs = [{"text": "🧪 Оригинал\n第二行", "urls": ["https://example.test/original"]}]
    original_inputs = deepcopy(inputs)
    output = tmp_path / "reports"
    first_json, first_md = render.persist(typed_payload, output, "weekly-owned", inputs=inputs)
    prior = {path: path.read_bytes() for path in first_json.parent.iterdir()}
    custom = Mock(return_value="Saved custom Markdown · новости\n")
    other_json, other_md = render.persist(
        typed_payload, output, "custom-owned", render_markdown=custom, inputs=inputs,
    )
    custom.assert_called_once_with(typed_payload)
    assert first_json.read_bytes() == other_json.read_bytes()
    assert json.loads(first_json.read_text(encoding="utf-8")) == before
    assert first_md.read_text(encoding="utf-8") == render.markdown(typed_payload)
    assert other_md.read_text(encoding="utf-8") == "Saved custom Markdown · новости\n"
    assert (first_json.parent / "inputs.jsonl").read_bytes() == (
        other_json.parent / "inputs.jsonl"
    ).read_bytes()
    assert (first_json.parent / "inputs.jsonl").read_text(encoding="utf-8") == (
        json.dumps(inputs[0], ensure_ascii=False, allow_nan=False) + "\n"
    )
    assert {path.name for path in first_json.parent.iterdir()} == {
        "weekly-owned.json", "weekly-owned.md", "inputs.jsonl",
    }
    assert first_json.parent == first_md.parent == output / "weekly-owned"
    assert other_json.parent == other_md.parent == output / "custom-owned"
    assert {path.name for path in output.iterdir()} == {"weekly-owned", "custom-owned"}
    assert all(path.read_bytes() == content for path, content in prior.items())
    assert typed_payload == before and inputs == original_inputs


def test_custom_news_renderer_bypasses_paper_projection(tmp_path, monkeypatch):
    payload = {
        "metadata": {"source": "telegram"},
        "cards": [{"source_id": "telegram:101:7", "summary": "Новости · 🧪"}],
    }
    before = deepcopy(payload)
    paper = Mock(side_effect=AssertionError("news must not use paper Markdown"))
    news = Mock(return_value="Новости · 🧪\n")
    monkeypatch.setattr(render, "markdown", paper)
    json_path, md_path = render.persist(
        payload, tmp_path / "news", "news-owned", render_markdown=news,
    )
    news.assert_called_once_with(payload)
    paper.assert_not_called()
    assert md_path.read_text(encoding="utf-8") == "Новости · 🧪\n"
    assert json.loads(json_path.read_text(encoding="utf-8")) == before
    assert payload == before
