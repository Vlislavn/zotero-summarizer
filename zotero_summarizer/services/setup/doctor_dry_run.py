"""Doctor's write-free triage callback and its scoped local client lifetime."""
from typing import Any

from zotero_summarizer.models import SummarizeRequest
from zotero_summarizer.models.providers import resolve_stage
from zotero_summarizer.services._common import read_config
from zotero_summarizer.services.setup.doctor_environment import _row
from zotero_summarizer.services.setup.load_attempt import SetupAttempt, load_check_row, run_setup_load
from zotero_summarizer.settings import Settings


def _dry_run(settings: Settings, *, attempt=None) -> dict[str, Any]:
    from zotero_summarizer.services.llm.factory import build_client_for_stage
    from zotero_summarizer.services.triage.summarization import run_abstract_dry

    config = read_config(settings.config_path, settings.calibration_path)
    if not config.llm_enabled:
        return _row("dry_run", "ready", "LLM dry-run skipped by choice",
                    "The classifier-only backlog path does not call an LLM.")
    attempt = attempt or SetupAttempt()
    resolved = resolve_stage(config.llm_routing, "feed")
    request = SummarizeRequest(
        title="Dry-run paper: reproducible local research triage",
        abstract=(
            "We evaluate a reproducible paper-triage method with held-out data, "
            "ablation studies, confidence intervals, and public code."
        ),
    )

    def load(submit):
        client = build_client_for_stage(resolved,
            **({"request_observer": attempt} if resolved.provider.is_local else {}))
        try:
            submit()
            result = run_abstract_dry(config, request, client)
            return {"status": "operational", "detail": f"priority={result.reading_priority}; score={result.relevance_score}"}
        finally:
            if resolved.provider.is_local:
                client.close()

    result = run_setup_load(settings, resolved, load, attempt=attempt)
    return load_check_row("dry_run", [{"provider": resolved.provider.name,
        "model": resolved.model, "stage": resolved.stage, **result}],
        "Dry-run triage completed without a Zotero write")
