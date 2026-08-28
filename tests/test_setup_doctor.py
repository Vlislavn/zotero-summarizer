import json
import pytest
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.setup.bootstrap import bootstrap_phase0
from zotero_summarizer.services.setup import doctor
from zotero_summarizer.settings import Settings


def _settings(tmp_path):
    settings = Settings.load(project_root=tmp_path)
    bootstrap_phase0(settings)
    return settings


def test_doctor_persists_shared_contract_and_redacts_secrets(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret")
    runners = {check_id: (lambda _settings, check_id=check_id: doctor._row(
        check_id, "ready", f"{check_id} passed", "super-secret detail",
    )) for check_id in doctor._CHECKS}
    monkeypatch.setattr(doctor, "_RUNNERS", runners)
    result = doctor.run_doctor(settings)
    assert result["ready"]
    assert result["modes"] == {
        "local_inference": "ready", "offline_ready": "ready", "strict_offline": "not_started",
    }
    persisted = json.loads((settings.data_dir / "setup_doctor.json").read_text())
    assert persisted == result
    assert "super-secret" not in json.dumps(result)
    assert all(set(row) == {"id", "status", "message", "detail", "recovery"}
               for row in result["checks"])


def test_doctor_failure_is_actionable_and_single_check_retry_preserves_rows(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(doctor, "_RUNNERS", {check_id: (
        lambda _settings, check_id=check_id: doctor._row(check_id, "ready", "ok")
    ) for check_id in doctor._CHECKS})
    doctor.run_doctor(settings)
    monkeypatch.setitem(doctor._RUNNERS, "zotero", lambda _settings: (_ for _ in ()).throw(RuntimeError("locked")))
    result = doctor.run_doctor(settings, check_ids=["zotero"])
    rows = {row["id"]: row for row in result["checks"]}
    assert rows["zotero"]["status"] == "needs_action"
    assert rows["zotero"]["recovery"]["label"] == "Retry"
    assert rows["database"]["status"] == "ready"


def test_local_modes_require_real_inference(tmp_path):
    settings = _settings(tmp_path)
    rows = [doctor._row(check_id, "ready", "ok") for check_id in doctor._CHECKS]
    next(row for row in rows if row["id"] == "llm_inference")["status"] = "needs_action"

    modes = doctor._modes(settings, rows)
    assert modes["local_inference"] == modes["offline_ready"] == "needs_action"
    assert modes["strict_offline"] == "not_started"


def test_inference_check_preserves_distinct_failures_and_local_recovery(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    config = doctor.read_config(settings.config_path)
    resolved = doctor.resolve_stage(config.llm_routing, "feed")

    async def _failed(_routing):
        return {"stages": [
            {"stage": "feed", "provider": resolved.provider.name, "model": resolved.model,
             "status": "fail", "detail": "timeout after 60s"},
            {"stage": "backlog", "provider": resolved.provider.name, "model": resolved.model,
             "status": "fail", "detail": "timeout after 60s"},
            {"stage": "deep_review", "provider": resolved.provider.name, "model": resolved.model,
             "status": "fail", "detail": "model does not support chat"},
        ]}

    from zotero_summarizer.services.llm import operational_check
    monkeypatch.setattr(operational_check, "check_routing_stages", _failed)
    row = doctor._llm_inference(settings)

    assert row["detail"].count(f"{resolved.provider.name}/{resolved.model}") == 2
    assert "feed, backlog" in row["detail"] and "deep_review" in row["detail"]
    assert row["recovery"]["label"] == "Refresh model"
    assert row["recovery"]["command"] == f"ollama pull {resolved.model}"


def test_doctor_rejects_unknown_check_and_marks_interrupted_run(tmp_path):
    settings = _settings(tmp_path)
    with pytest.raises(APIError) as error:
        doctor.run_doctor(settings, check_ids=["made_up"])
    assert error.value.status_code == 422
    payload = doctor.doctor_status(settings)
    payload["checks"] = payload["checks"][1:]
    payload["checks"][0]["status"] = "running"
    (settings.data_dir / "setup_doctor.json").write_text(json.dumps(payload))
    resumed = doctor.doctor_status(settings)
    assert {row["id"] for row in resumed["checks"]} == set(doctor._CHECKS)
    assert resumed["checks"][1]["status"] == "needs_action"
