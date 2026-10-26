"""Unknown evidence is publishable uncertainty, not a failed whole paper."""
import json
from pathlib import Path

import pytest

from tests.test_feed_engineering import _assessment, _generated_entry
from tests.test_research_feed import _seed, _settings
from tests.test_research_feed_boundaries import START, END
from zotero_summarizer.services.research_feed import runner
from zotero_summarizer.services.research_feed.profile import load_profile


@pytest.mark.parametrize("applicable_neighbor", [True, False])
def test_unknown_project_spans_survive_card_and_publication(tmp_path, monkeypatch, applicable_neighbor):
    settings = _settings(tmp_path)
    _seed(settings, "paper", "Agent evaluation")
    profile = load_profile(settings.data_dir)
    content = _assessment("paper", projects=tuple(profile.projects))
    for index, project in enumerate(content["projects"]):
        if index or not applicable_neighbor:
            project.update(assessment="unknown", use_kind=None, rationale="Source is insufficient",
                           mechanism="", target_component="", proposed_change="",
                           minimal_validation_experiment="", evidence_quote="")
    entry, generator, verifier = _generated_entry(tmp_path, monkeypatch, "paper", profile, content=content)

    result = runner.run_weekly(settings, start=START, end=END, review_loader=lambda _key: entry)
    report = json.loads(Path(result["json_path"]).read_text())

    assert result["cards_generated"] == 1, report["failed"]
    assert report["failed"] == []
    card = report["cards"][0]["card"]
    for index, project in enumerate(content["projects"]):
        span = card["assessment_evidence"][f"/projects/{index}"]
        if project["assessment"] == "unknown":
            assert span is None
        else:
            assert span["quote"] == project["evidence_quote"]
    assert bool(card["project_uses"]) == applicable_neighbor
    assert bool(card["research_ideas"]) == applicable_neighbor
    assert len(generator.calls) == verifier.calls == 1
