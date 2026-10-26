"""Typed capability rejection cannot erase an observed unresolved predecessor."""
import pytest

from tests.test_setup_load_ownership16 import boundary, routing  # noqa: F401
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services import _common
from zotero_summarizer.services.llm import factory
from zotero_summarizer.services.setup import doctor, load_attempt as la


@pytest.mark.parametrize("operation", ["execute", "status"])
def test_doctor_hold_with_unsupported_feed_remains_not_ready_without_inference(monkeypatch, boundary, operation):
    original = la.SetupAttempt()
    boundary.start(boundary.ticket(original))
    routes = routing()
    routes.providers[0].capabilities = frozenset()
    config = _common.read_config(boundary.settings.config_path, boundary.settings.calibration_path)
    config.llm_routing, config.llm_enabled = routes, True
    _common.write_user_config(boundary.settings.config_path, config)
    monkeypatch.setattr(factory, "resolve_api_key", lambda _: pytest.fail("Unsupported capability resolved a credential"))
    with pytest.raises(APIError) as error:
        factory.build_client_for_provider(routes.providers[0], "synthetic", verification=True)
    assert error.value.error == "unsupported_capabilities"
    for key in doctor._CHECKS:
        monkeypatch.setitem(doctor._RUNNERS, key,
                            lambda _settings, k=key: doctor._row(k, "ready", "synthetic cached success"))
    if operation == "execute":
        result = doctor.run_doctor(boundary.settings, check_ids=["llm_inference", "dry_run"])
    else:
        doctor._save(boundary.settings, {
            "status": "ready", "ready": True, "verification_revision": doctor.ADMISSION_REVISION,
            "checks": [doctor._row(key, "ready", "synthetic cached success") for key in doctor._CHECKS],
            "config_signature": doctor._config_signature(boundary.settings),
        })
        result = doctor.doctor_status(boundary.settings)
    assert not result["ready"] and result["status"] == "needs_action" and result["request_hold"]
    rows = {row["id"]: row for row in result["checks"]}
    for key in ("llm_inference", "dry_run"):
        assert rows[key]["status"] == "needs_action"
        assert rows[key]["recovery"]["action"] == "open_ai_settings"
    assert boundary.held()
