"""Successful abstract-stage cache; cards are cheap projections, never report replay."""
from __future__ import annotations

import json
from pathlib import Path

from zotero_summarizer.models import SummarizeRequest, SummarizeResponse
from zotero_summarizer.models.providers import resolve_stage
from zotero_summarizer.services._common import now_iso_z, read_config, state, write_json_atomic
from zotero_summarizer.services.library._feed_assessment import fingerprint
from zotero_summarizer.services.llm import factory


class CandidateCursor:
    def __init__(self, settings):
        self.path = settings.data_dir / "research_feed" / "triage.json"
        self.settings = settings
        if self.path.parent.is_symlink() or self.path.is_symlink():
            raise ValueError("feed triage cache cannot be a symlink")
        self.entries = json.loads(self.path.read_text()) if self.path.exists() else {}
        if not isinstance(self.entries, dict) or len(self.entries) > 5000:
            raise ValueError("malformed feed triage cache")

    def row(self, candidate, profile, *, generate=True):
        if not generate and candidate.source_id not in self.entries:
            raise ValueError("cached abstract triage unavailable")
        app = state() if generate else None
        config = app.app_state.config if generate else read_config(self.settings.config_path, self.settings.calibration_path)
        config = config.model_copy(update={"research_goals": [*profile.themes, *profile.projects]})
        resolved = resolve_stage(config.llm_routing, "feed")
        sources = Path(__file__).parents[1]
        identity = fingerprint({"candidate": candidate.model_dump(mode="json"), "config": config.model_dump(mode="json"),
            "profile": profile.model_dump(mode="json"), "model": resolved.model_dump(mode="json"),
            "schema": SummarizeResponse.model_json_schema(), "timeout_seconds": self.settings.summary_timeout_seconds,
            "sources": {name: (sources / name).read_text() for name in
                        ("triage/summarization.py", "triage/prompts.py", "triage/_json_output.py", "model/scoring.py")}})
        entry = self.entries.get(candidate.source_id)
        if entry is not None and not isinstance(entry, dict):
            raise ValueError("malformed feed triage entry")
        if entry is None or entry.get("input_sha256") != identity:
            if not generate:
                raise ValueError("cached abstract triage source/profile/config identity stale")
            if not candidate.abstract.strip():
                raise ValueError("abstract unavailable for triage")
            from zotero_summarizer.services.triage.summarization import run_abstract_dry
            summary = run_abstract_dry(config, SummarizeRequest(title=candidate.title, abstract=candidate.abstract,
                                      doi=candidate.doi), factory.resolve_stage_client(app, "feed"))
            entry = {"input_sha256": identity, "summary": summary.model_dump(mode="json"),
                     "generated_at": now_iso_z(), "model": resolved.model, "provider": resolved.provider.name,
                     "basis": "abstract_only"}
            entry["payload_sha256"] = fingerprint(entry)
            self.entries.pop(candidate.source_id, None)
            self.entries[candidate.source_id] = entry
            while len(self.entries) > 5000:
                del self.entries[next(iter(self.entries))]
            write_json_atomic(self.path, self.entries)
        if entry.get("payload_sha256") != fingerprint({k: v for k, v in entry.items() if k != "payload_sha256"}):
            raise ValueError("feed triage cache checksum mismatch")
        summary = SummarizeResponse.model_validate(entry["summary"], strict=True).model_dump(mode="json")
        return {"decision": "triaged_pending", "composite_score": summary["composite_relevance_score"],
                "reading_priority": summary["reading_priority"], "shap_contribs_json": json.dumps({"summary": summary}),
                "triage_provenance": {key: entry[key] for key in ("input_sha256", "generated_at", "model", "provider", "basis")}}
