from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import TYPE_CHECKING, Any

from zotero_summarizer.settings import Settings

if TYPE_CHECKING:
    import asyncio
    import threading

    from zotero_summarizer.integrations.openalex import OpenAlexClient
    from zotero_summarizer.integrations.openalex_cache import OpenAlexCache
    from zotero_summarizer.integrations.openreview import OpenReviewClient
    from zotero_summarizer.integrations.pdf import OnPremPdfExtractor
    from zotero_summarizer.integrations.unpaywall import UnpaywallClient
    from zotero_summarizer.integrations.zotero_read import ZoteroReader
    from zotero_summarizer.integrations.zotero_write import ZoteroWriter
    from zotero_summarizer.models import AppState
    from zotero_summarizer.storage.corpus import EmbeddingCache


@dataclass
class RuntimeState:
    """Typed bag of process-wide singletons wired up by ``lifecycle.startup``.

    Replaces the old ``SimpleNamespace`` so attribute names are declared in one
    place and type-checkers/IDEs can see them. Fields default to a not-yet-wired
    value (``None`` / empty) so a partially-initialised state is well-defined.

    Annotations are lazy (``from __future__ import annotations`` + the
    ``TYPE_CHECKING`` block) so this foundational module never imports the
    integrations/storage layers at runtime — keeping it cycle-free.
    """

    # The validated config holder (``AppState.config`` is the GoalsConfig).
    app_state: "AppState | None" = None

    # PDF + embedding singletons. Stage clients are cached below;
    # services.llm.factory owns their lazy resolution and construction.
    pdf_extractor: "OnPremPdfExtractor | None" = None
    embedding_cache: "EmbeddingCache | None" = None

    # External-metadata clients (optional; None when the feature is disabled).
    openalex_client: "OpenAlexClient | None" = None
    openalex_cache: "OpenAlexCache | None" = None
    unpaywall_client: "UnpaywallClient | None" = None
    openreview_client: "OpenReviewClient | None" = None

    # Hybrid daemon classifier gate. Typed loosely (Any) to keep this module
    # independent of the services/model layer.
    classifier_gate: Any = None
    classifier_gate_lock: "threading.Lock | None" = None
    classifier_gate_training: bool = False
    # Last gate (re)train failure reason (empty when healthy). Mirrors
    # ``zotero_error`` so readiness checks can report WHY the gate is None
    # (e.g. "ModuleNotFoundError: No module named 'lightgbm'") instead of
    # leaving it a silent None.
    classifier_gate_error: str = ""

    # Zotero local integration (None when unavailable; reason in zotero_error).
    zotero_reader: "ZoteroReader | None" = None
    zotero_writer: "ZoteroWriter | None" = None
    zotero_error: str = ""

    # In-flight / persisted triage jobs, keyed by job_id.
    triage_jobs: dict[str, Any] = field(default_factory=dict)

    # Serialises concurrent corpus writes (set to an asyncio.Lock at startup).
    corpus_write_lock: "asyncio.Lock | None" = None

    # Effective routing is part of the key; invalidation also fences in-flight builds.
    _stage_clients: dict[Any, Any] = field(default_factory=dict)
    _stage_clients_generation: int = 0
    _stage_clients_lock: Any = field(default_factory=Lock, repr=False)

    def resolve_stage_provider(self, stage: str) -> Any:
        """Return the resolved ``ProviderConfig`` for ``stage`` — the provider
        profile only, with no client build and no network. Cheap enough to call
        every tick to decide per-stage concurrency (local → serial)."""
        from zotero_summarizer.models.providers import resolve_stage

        routing = self.app_state.config.llm_routing
        return resolve_stage(routing, stage).provider

    def invalidate_stage_clients(self) -> None:
        """Drop cached stage clients so the next use rebuilds from current config
        (call after a config PUT swaps ``app_state``)."""
        with self._stage_clients_lock:
            self._stage_clients_generation += 1
            self._stage_clients.clear()


@dataclass
class AppContext:
    settings: Settings
    state: RuntimeState = field(default_factory=RuntimeState)


_context: AppContext | None = None


def set_context(context: AppContext) -> None:
    global _context
    _context = context


def get_context() -> AppContext:
    if _context is None:
        set_context(AppContext(settings=Settings.load()))
    assert _context is not None
    return _context
