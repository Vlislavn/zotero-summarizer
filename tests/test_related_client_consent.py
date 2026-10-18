"""Exercise the real config-swap/cache interleaving with inert model clients."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Event
from types import SimpleNamespace

from test_related_saved_papers import Model
from zotero_summarizer.models import AppState, GoalsConfig
from zotero_summarizer.models.config import LLMConfig
from zotero_summarizer.models.providers import LLMRoutingConfig
from zotero_summarizer.runtime import RuntimeState
from zotero_summarizer.services import config as config_service
from zotero_summarizer.services.library import _related_assessment as semantic
from zotero_summarizer.services.llm import factory
from zotero_summarizer.settings import Settings

BARRIER_CAP_SECONDS = float(os.getenv("ZS_TEST_BARRIER_CAP_SECONDS", "5"))
assert 0 < BARRIER_CAP_SECONDS <= 30


def test_private_payload_never_uses_client_built_before_actual_config_swap(tmp_path, monkeypatch):
    routing = LLMRoutingConfig.model_validate({
        "providers": [{"name": "provider-A", "base_url": "http://127.0.0.1:9998/v1", "api_key_env": "TEST_A"}],
        "default": {"provider": "provider-A", "model": "model-A"},
    })
    config_a = GoalsConfig(relevance_scale={n: f"Synthetic {n}" for n in range(1, 6)}, llm_routing=routing,
                           llm=LLMConfig(api_base="http://127.0.0.1:9998/v1", api_key_env="TEST_A",
                                         draft_model="model-A", refine_model="model-A"))
    config_a.corpus.enabled = False
    config_b = config_a.model_copy(deep=True)
    config_b.llm_routing.providers[0].name = "provider-B"
    config_b.llm_routing.providers[0].base_url = "http://127.0.0.1:9997/v1"
    config_b.llm_routing.default.provider = "provider-B"
    config_b.llm_routing.default.model = "model-B"
    app = RuntimeState(app_state=AppState(config=config_a))
    selected = Settings.load(project_root=tmp_path)
    selected.data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_service, "settings", lambda: selected)
    monkeypatch.setattr(config_service, "state", lambda: app)
    monkeypatch.setattr(config_service, "EmbeddingCache", lambda path, name: SimpleNamespace(model_name=name))
    monkeypatch.setattr(semantic, "state", lambda: app)
    monkeypatch.setattr(semantic, "offline_requested", lambda: False)
    model_a, model_b = Model(), Model()
    entered_a, release_a = Event(), Event()

    def build(resolved):
        if resolved.model == "model-A":
            entered_a.set()
            assert release_a.wait(BARRIER_CAP_SECONDS), "test-owned A builder was not released"
            return model_a
        assert resolved.model == "model-B"
        return model_b

    monkeypatch.setattr(factory, "build_client_for_stage", build)
    with ThreadPoolExecutor(max_workers=1) as pool:
        old_build = pool.submit(app.resolve_stage_client, "deep_review")
        try:
            assert entered_a.wait(BARRIER_CAP_SECONDS), "test-owned A builder was not entered"
            changed = asyncio.run(config_service.update_runtime_config(config_b))
            assert changed["status"] == "ok"
        finally:
            release_a.set()
        assert old_build.result(timeout=BARRIER_CAP_SECONDS) is model_a
    boundary_b = semantic.model_boundary()
    assert boundary_b["provider"] == "provider-B" and boundary_b["model"] == "model-B"
    seed = {"item_key": "SEED0001", "title": "Seed paper", "abstract": "Seed metadata.",
            "comments": {"records": [{"id": "private-seed", "text": "PRIVATE TEST CONTEXT"}]}}
    candidate = {"item_key": "GOOD0001", "title": "Candidate", "abstract": "Candidate metadata.",
                 "comments": {"records": [{"id": "private-candidate", "text": "PRIVATE CANDIDATE CONTEXT"}]}}

    result = semantic.assess(seed, [candidate], boundary_b)

    assert result["status"] == "assessed"
    assert model_a.payloads == [], "a client built for unconsented A received private comments after consent to B"
    assert len(model_b.payloads) == 1
