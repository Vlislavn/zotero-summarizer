from __future__ import annotations

import asyncio

from fastapi import APIRouter

from zotero_summarizer.models.providers import ProviderConfig
from zotero_summarizer.services.llm import jev_engine, model_list, operational_check, connections, connection_credentials, connection_test, step_history
from zotero_summarizer.models.connections import ChecklistEngineTest, RegisterConnection, ConnectionSelection, ReplaceConnectionCredential, ModelTestRequest, TaskModelsTestRequest

router = APIRouter()
# Manual "is it operational" probe: checks each pipeline stage's configured
# provider and returns per-stage operational|fail. The app starts regardless of
# provider availability; this is how the user verifies stages on demand.
router.add_api_route("/api/admin/llm-check", operational_check.check_stages, methods=["POST"])
# Cheap proactive reachability (GET /models, no tokens): the deep-review surface
# polls this on mount so a dead endpoint shows a banner before a run, instead of
# the run silently producing no digest.
router.add_api_route("/api/admin/llm-reachability", operational_check.check_reachability, methods=["GET"])
# Median time + last success per step from logs the app already writes (Settings models table).
router.add_api_route("/api/llm/step-history", step_history.step_history, methods=["GET"])


async def list_provider_models(provider: ProviderConfig) -> dict:
    """List the models a provider serves, for the Settings model-picker.

    Body is one provider profile (from the in-progress edit, not the saved
    config) so the user can pick a model before saving. The listing makes a
    blocking network call, so it runs in a worker thread to keep the event loop
    free — the same reason the operational check offloads its probes.
    """
    models = await asyncio.to_thread(model_list.list_models_for_provider, provider)
    return {"provider": provider.name, "type": provider.type.value, "models": models}


# Model discovery for the Settings picker: POST a provider profile, get its
# available model ids (OpenAI-compatible /models or the Anthropic models list).
router.add_api_route("/api/admin/llm-models", list_provider_models, methods=["POST"])


# Sync endpoints run in FastAPI's worker pool; metadata/locks never wait on the event loop.
def register_connection(command: RegisterConnection):
    return connections.register(command)


def preview_connection(command: ConnectionSelection):
    return connections.preview(command)


def apply_connection(command: ConnectionSelection):
    return connections.apply(command)


def test_saved_model(command: ModelTestRequest):
    return connection_test.test_connection(command)


def test_saved_tasks(command: TaskModelsTestRequest):
    return connection_test.test_tasks(command)


def replace_connection_credential(command: ReplaceConnectionCredential):
    return connection_credentials.replace(command)



def checklist_engine_status():
    return {'key_present': jev_engine.key_present()}


async def test_checklist_engine(command: ChecklistEngineTest):
    """Save a given Cloud key (OS keyring, never echoed), then ask the engine one tiny question."""
    from zotero_summarizer.services._common import settings
    from zotero_summarizer.services.setup.doctor import store_credential_and_invalidate

    if command.engine == 'cloud' and command.secret:
        await asyncio.to_thread(store_credential_and_invalidate, settings(), jev_engine.KEY_NAME, command.secret, impact_approved=True)
    result = await asyncio.to_thread(jev_engine.check, command.engine, command.url)
    return {**result, 'key_present': jev_engine.key_present()}


router.add_api_route('/api/llm/checklist-engine', checklist_engine_status, methods=['GET'])
router.add_api_route('/api/llm/checklist-engine/test', test_checklist_engine, methods=['POST'])
router.add_api_route('/api/llm/connections', connections.context, methods=['GET'])
router.add_api_route('/api/llm/connections', register_connection, methods=['POST'])
router.add_api_route('/api/llm/connections/impact', preview_connection, methods=['POST'])
router.add_api_route('/api/llm/connections/apply', apply_connection, methods=['POST'])
router.add_api_route('/api/llm/connections/test', test_saved_model, methods=['POST'])
router.add_api_route('/api/llm/connections/test-tasks', test_saved_tasks, methods=['POST'])
router.add_api_route('/api/llm/connections/credential', replace_connection_credential, methods=['POST'])
router.add_api_route('/api/llm/connections/credential/{token}', connection_credentials.readback, methods=['GET'])
router.add_api_route('/api/llm/connections/{operation_id}', connections.readback, methods=['GET'])
router.add_api_route('/api/llm/connections/{operation_id}/catalog', connections.catalog, methods=['POST'])
