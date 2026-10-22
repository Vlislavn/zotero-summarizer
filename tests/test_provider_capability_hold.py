"""Typed capability rejection remains a diagnostic while local finality is on HOLD."""
import pytest

from test_setup_load_ownership16 import boundary, routing
from zotero_summarizer.services import _common
from zotero_summarizer.services.setup import doctor
from zotero_summarizer.services.setup import load_attempt as la
from zotero_summarizer.services.setup.bootstrap import bootstrap_phase0
from zotero_summarizer.settings import Settings


@pytest.mark.parametrize("operation", ["execute", "status"])
def test_doctor_hold_with_unsupported_feed_remains_not_ready_without_inference(
    tmp_path, monkeypatch, boundary, operation,
):
    settings = Settings.load(project_root=tmp_path)
    bootstrap_phase0(settings)
    routes = routing()
    routes.providers[0].capabilities = frozenset()
    config = _common.read_config(settings.config_path, settings.calibration_path)
    config.llm_routing, config.llm_enabled = routes, True
    monkeypatch.setattr(_common, "read_config", lambda *args: config)
    with boundary.state:
        boundary.hold = "synthetic_finality_unproven"
    for key in doctor._CHECKS:
        monkeypatch.setitem(doctor._RUNNERS, key,
                            lambda _settings, k=key: doctor._row(k, "ready", "synthetic cached success"))
    if operation == "execute":
        result = doctor.run_doctor(settings, check_ids=["llm_inference", "dry_run"])
    else:
        _common.write_json_atomic(settings.data_dir / "setup_doctor.json", {
            "status": "ready", "ready": True,
            "checks": [doctor._row(key, "ready", "synthetic cached success") for key in doctor._CHECKS],
            "config_signature": doctor._config_signature(settings),
        })
        result = doctor.doctor_status(settings)
    assert result["ready"] is False and result["status"] == "needs_action"
    rows = {row["id"]: row for row in result["checks"]}
    for key in ("llm_inference", "dry_run"):
        assert rows[key]["status"] == "needs_action"
        assert rows[key]["recovery"]["action"] == "open_ai_settings"
    assert la.local_load_hold() == "synthetic_finality_unproven"
