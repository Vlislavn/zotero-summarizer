import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.test_research_feed import _review, _seed, _settings
from zotero_summarizer.services.research_feed.runner import run_weekly


@pytest.mark.parametrize("has_code", [False, True])
@pytest.mark.parametrize("scientific_gap", [False, True])
def test_weekly_artifact_availability_does_not_authorize_reproduction_effort(
    tmp_path, has_code, scientific_gap,
):
    settings = _settings(tmp_path)
    _seed(settings, "evidence-paper", "Agent evaluation method")
    gap = "No held-out comparison is reported"

    def reviewed(key):
        result = _review(key)
        if not has_code:
            result["code_link"] = {"found": False, "exists": False, "relevance": "missing"}
        if scientific_gap:
            result["quality"]["missing_critical"] = [gap]
        return result

    result = run_weekly(
        settings, start=datetime(2026, 8, 20, tzinfo=timezone.utc),
        end=datetime(2026, 8, 29, tzinfo=timezone.utc), dry_run=True,
        queue_zotero=False, review_loader=reviewed,
    )
    payload = json.loads(Path(result["json_path"]).read_text())
    assert result["cards_generated"] == len(payload["cards"]) == 1
    card = payload["cards"][0]["card"]
    assert card["reproducibility_tier"] == "unknown"
    assert card["missing_reproduction_inputs"] == []
    assert card["evidence_gaps"] == ([gap] if scientific_gap else [])
    assert card["code_urls"] == (["https://github.com/example/evidence-paper"] if has_code else [])
    assert card["worth_reading"] == "skim"
    assert result["writebacks_queued"] == 0
    assert Path(result["markdown_path"]).is_file()
    assert Path(result["markdown_path"]).stat().st_size > 0
