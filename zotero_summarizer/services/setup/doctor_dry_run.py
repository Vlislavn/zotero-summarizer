"""Doctor's owned no-write abstract pipeline; no corpus or native Zotero effects."""
from typing import Any

from zotero_summarizer.models import SummarizeRequest
from zotero_summarizer.models.providers import resolve_stage
from zotero_summarizer.services._common import read_config
from zotero_summarizer.services.setup.doctor_environment import _row
from zotero_summarizer.services.setup.load_admission import refused_local_load
from zotero_summarizer.settings import Settings


def _dry_run(settings: Settings) -> dict[str, Any]:
    from zotero_summarizer.services.setup.load_attempt import SetupAttempt, current_attempt

    attempt = current_attempt() or SetupAttempt()
    with attempt.scope():
        return _run(settings, attempt)


def _run(settings, attempt):
    from zotero_summarizer.services.llm.factory import build_client_for_stage
    from zotero_summarizer.services.triage.summarization import run_abstract_dry

    config = read_config(settings.config_path, settings.calibration_path)
    if not config.llm_enabled:
        return _row(
            "dry_run",
            "ready",
            "LLM dry-run skipped by choice",
            "The classifier-only backlog path does not call an LLM.",
        )
    resolved = resolve_stage(config.llm_routing, "feed")
    refusal = refused_local_load(resolved.provider, resolved.model)
    if refusal:
        row = _row("dry_run", "needs_action", "Local dry-run was not submitted", refusal["detail"], action="Review AI settings")
        row["recovery"]["action"] = "open_ai_settings"
        row["load_receipts"] = [refusal]
        return row
    request = SummarizeRequest(
        title="Dry-run paper: reproducible local research triage",
        abstract=(
            "We evaluate a reproducible paper-triage method with held-out data, "
            "ablation studies, confidence intervals, and public code."
        ),
    )
    before = attempt.requests.summary()["expected"]
    client = build_client_for_stage(resolved, verification=True)
    try:
        result = run_abstract_dry(config, request, client)
    finally:
        client.close()
    completion = attempt.requests.summary(start=before)
    if not completion["successful"]:
        raise ValueError("Dry-run has no coherent owned native request inventory")
    detail = f"priority={result.reading_priority}; score={result.relevance_score}"
    row = _row("dry_run", "ready", "Dry-run triage completed without a Zotero write", detail)
    row["load_receipts"] = [{"status": "operational", "ownership": "completed", "request_completion": completion}]
    return row
