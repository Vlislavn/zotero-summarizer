import json
import sqlite3
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
    runners = {
        check_id: (
            lambda _settings, check_id=check_id: doctor._row(
                check_id,
                "ready",
                f"{check_id} passed",
                "super-secret detail",
            )
        )
        for check_id in doctor._CHECKS
    }
    monkeypatch.setattr(doctor, "_RUNNERS", runners)
    result = doctor.run_doctor(settings)
    assert result["ready"]
    assert result["modes"] == {
        "local_inference": "ready",
        "offline_ready": "ready",
        "strict_offline": "not_started",
    }
    persisted = json.loads((settings.data_dir / "setup_doctor.json").read_text())
    assert persisted == result
    assert "super-secret" not in json.dumps(result)
    assert all(
        set(row) == {"id", "status", "message", "detail", "recovery"}
        for row in result["checks"]
    )


def test_doctor_failure_is_actionable_and_single_check_retry_preserves_rows(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        doctor,
        "_RUNNERS",
        {
            check_id: (
                lambda _settings, check_id=check_id: doctor._row(
                    check_id, "ready", "ok"
                )
            )
            for check_id in doctor._CHECKS
        },
    )
    doctor.run_doctor(settings)
    monkeypatch.setitem(
        doctor._RUNNERS,
        "zotero",
        lambda _settings: (_ for _ in ()).throw(RuntimeError("locked")),
    )
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


def test_doctor_readiness_is_invalidated_by_changed_setup_config(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(doctor, "_RUNNERS", {
        check_id: (lambda _settings, check_id=check_id: doctor._row(check_id, "ready", "ok"))
        for check_id in doctor._CHECKS
    })
    assert doctor.run_doctor(settings)["ready"]
    settings.config_path.write_text(settings.config_path.read_text() + "\n# revised\n")
    assert doctor.doctor_status(settings)["ready"] is False
    assert doctor.doctor_status(settings)["status"] == "not_started"


def test_doctor_withholds_ready_until_saved_zotero_path_takes_effect(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(doctor, "_RUNNERS", {
        check_id: (lambda _settings, check_id=check_id: doctor._row(check_id, "ready", "ok"))
        for check_id in doctor._CHECKS
    })
    assert doctor.run_doctor(settings)["ready"]
    new_zotero = tmp_path / "next-zotero"
    new_zotero.mkdir()
    settings.env_path.write_text(f"ZOTERO_DATA_DIR={new_zotero}\n")
    stale = doctor.doctor_status(settings)
    assert stale["ready"] is False and stale["status"] == "needs_action"
    assert "Restart app" in stale["checks"][0]["message"]
    assert doctor.run_doctor(settings)["ready"] is False
    reloaded = Settings.load(project_root=tmp_path)
    assert reloaded.zotero_data_dir == new_zotero
    assert doctor.run_doctor(reloaded)["ready"] is True


def test_offline_probe_records_and_blocks_real_socket_attempt(monkeypatch):
    import socket
    from zotero_summarizer.services.setup import assets

    def fake_asset_report(_settings, *, load):
        assert load
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            with pytest.raises(OSError, match="network connection attempted"):
                sock.connect(("127.0.0.1", 11434))
        return {"offline_ready": True, "loadable": True, "models": []}

    monkeypatch.setattr(assets, "asset_report", fake_asset_report)
    result = assets.offline_probe_main(object())
    assert result["network_attempts"] == ["('127.0.0.1', 11434)"]


def test_uncaught_offline_socket_attempt_keeps_actionable_doctor_evidence(tmp_path, monkeypatch):
    import socket
    from zotero_summarizer.services.setup import assets

    def fake_asset_report(_settings, *, load):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.connect(("127.0.0.1", 11434))

    monkeypatch.setattr(assets, "asset_report", fake_asset_report)
    report = assets.offline_probe_main(object())
    assert report["network_attempts"] == ["('127.0.0.1', 11434)"]
    monkeypatch.setattr(assets, "offline_asset_report", lambda _: report)
    row = doctor._ml_assets(_settings(tmp_path))
    assert row["status"] == "needs_action"
    assert "network access" in row["message"]


def test_strict_offline_requires_a_network_attempt_free_cache_load(tmp_path, monkeypatch):
    from zotero_summarizer.services import _common
    from zotero_summarizer.services._common import write_user_config
    from zotero_summarizer.services.setup import assets
    from zotero_summarizer.services.setup.profiles import apply_local_profile

    settings = _settings(tmp_path)
    config = apply_local_profile(_common.read_config(settings.config_path), "light")
    write_user_config(settings.config_path, config)
    monkeypatch.setenv("ZS_OFFLINE", "1")
    monkeypatch.setattr(
        assets,
        "offline_asset_report",
        lambda _settings: {
            "offline_ready": True,
            "loadable": True,
            "models": [{"repo_id": "cached/model", "cached": True}],
            "network_attempts": ["huggingface.co"],
        },
    )

    asset_check = doctor._ml_assets(settings)
    rows = [doctor._row(check_id, "ready", "ok") for check_id in doctor._CHECKS]
    rows[doctor._CHECKS.index("ml_assets")] = asset_check

    assert asset_check["status"] == "needs_action"
    assert "network" in asset_check["message"].lower()
    assert doctor._modes(settings, rows)["strict_offline"] == "needs_action"


def test_ml_only_doctor_skips_ai_checks_by_choice(tmp_path):
    from zotero_summarizer.services._common import read_config, write_user_config

    settings = _settings(tmp_path)
    config = read_config(settings.config_path)
    write_user_config(
        settings.config_path,
        config.model_copy(update={"llm_enabled": False}),
    )
    rows = [doctor._row(check_id, "ready", "ok") for check_id in doctor._CHECKS]

    assert doctor._runtime_model(settings)["status"] == "ready"
    assert doctor._llm_inference(settings)["status"] == "ready"
    assert doctor._dry_run(settings)["status"] == "ready"
    assert doctor._local_profile(settings)["status"] == "unavailable"
    assert doctor._modes(settings, rows)["local_inference"] == "unavailable"


def test_inference_check_preserves_distinct_failures_and_local_recovery(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path)
    config = doctor.read_config(settings.config_path)
    resolved = doctor.resolve_stage(config.llm_routing, "feed")

    async def _failed(_routing):
        return {
            "stages": [
                {
                    "stage": "feed",
                    "provider": resolved.provider.name,
                    "model": resolved.model,
                    "status": "fail",
                    "detail": "timeout after 60s",
                },
                {
                    "stage": "backlog",
                    "provider": resolved.provider.name,
                    "model": resolved.model,
                    "status": "fail",
                    "detail": "timeout after 60s",
                },
                {
                    "stage": "deep_review",
                    "provider": resolved.provider.name,
                    "model": resolved.model,
                    "status": "fail",
                    "detail": "model does not support chat",
                },
            ]
        }

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
    payload["ready"] = True  # old persisted Ready from before the retry
    payload["status"] = "running"
    (settings.data_dir / "setup_doctor.json").write_text(json.dumps(payload))
    resumed = doctor.doctor_status(settings)
    assert {row["id"] for row in resumed["checks"]} == set(doctor._CHECKS)
    assert resumed["checks"][1]["status"] == "needs_action"
    assert resumed["ready"] is False and resumed["status"] == "needs_action"


def test_optional_browser_check_uses_the_actual_runtime_package(monkeypatch):
    monkeypatch.setattr(
        doctor.importlib.util,
        "find_spec",
        lambda name: object() if name == "patchright" else None,
    )
    assert doctor._optional_extras(None)["status"] == "ready"

    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda _name: None)
    missing = doctor._optional_extras(None)
    assert missing["status"] == "unavailable"
    assert missing["recovery"]["command"] == "uv sync --extra browser"


def test_rss_check_never_runs_schema_writes(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    with sqlite3.connect(settings.triage_db_path) as conn:
        conn.execute(
            "INSERT INTO rss_feeds (name, url) VALUES ('Feed', 'https://example.test/rss')"
        )
    from zotero_summarizer.storage import feeds

    monkeypatch.setattr(
        feeds,
        "open_triage_conn",
        lambda *_a: pytest.fail("doctor must be read-only"),
    )
    assert doctor._rss_source(settings)["message"] == "1 RSS source enabled"


def test_runtime_check_reports_a_stopped_ollama_service(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        doctor.shutil,
        "which",
        lambda name: "/usr/bin/ollama" if name == "ollama" else None,
    )
    from zotero_summarizer.services.llm import model_list

    monkeypatch.setattr(
        model_list,
        "list_models_for_provider",
        lambda _provider: (_ for _ in ()).throw(RuntimeError("refused")),
    )
    row = doctor._runtime_model(settings)
    assert row["status"] == "needs_action"
    assert row["message"] == "Ollama is not running"
    assert row["recovery"]["command"] == "ollama serve"


def test_doctor_fix_migrates_once(tmp_path, monkeypatch):
    from zotero_summarizer.storage import migrations

    settings = _settings(tmp_path)
    calls = []
    migrate = migrations.migrate_existing

    def counted(settings):
        calls.append(settings)
        return migrate(settings)

    monkeypatch.setattr(migrations, "migrate_existing", counted)
    monkeypatch.setitem(doctor._RUNNERS, "database", lambda _: doctor._row("database", "ready", "ok"))
    doctor.run_doctor(settings, check_ids=["database"], fix=True)
    assert calls == [settings]


def test_replacing_hosted_credential_invalidates_old_inference_success(tmp_path, monkeypatch):
    import asyncio
    from zotero_summarizer.api.routes import setup as setup_route
    from zotero_summarizer.services.llm import credentials

    settings = _settings(tmp_path)
    monkeypatch.setattr(doctor, "_RUNNERS", {
        check_id: (lambda _settings, check_id=check_id: doctor._row(check_id, "ready", "ok"))
        for check_id in doctor._CHECKS
    })
    assert doctor.run_doctor(settings)["ready"]
    monkeypatch.setattr(setup_route, "get_settings", lambda: settings)
    monkeypatch.setattr(credentials, "store_api_key", lambda name, key: {"name": name, "present": bool(key)})
    asyncio.run(setup_route.save_ai_credential(setup_route.CredentialRequest(name="HOSTED_KEY", api_key="replaced")))
    assert doctor.doctor_status(settings)["ready"] is False


def test_doctor_does_not_certify_config_changed_during_checks(tmp_path, monkeypatch):
    settings = _settings(tmp_path)

    def change_routing(_settings):
        settings.config_path.write_text(settings.config_path.read_text() + "\n# new routing revision\n")
        return doctor._row("llm_inference", "ready", "old revision passed")

    monkeypatch.setattr(doctor, "_RUNNERS", {
        check_id: (change_routing if check_id == "llm_inference"
                   else lambda _settings, check_id=check_id: doctor._row(check_id, "ready", "ok"))
        for check_id in doctor._CHECKS
    })
    result = doctor.run_doctor(settings)
    assert result["ready"] is False and result["status"] == "needs_action"
    assert result["checks"][0]["status"] == "needs_action"
    assert doctor.doctor_status(settings)["ready"] is False


def test_offline_mode_change_invalidates_cached_strict_offline_ready(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setenv("ZS_OFFLINE", "1")
    monkeypatch.setattr(doctor, "_RUNNERS", {
        check_id: (lambda _settings, check_id=check_id: doctor._row(check_id, "ready", "ok"))
        for check_id in doctor._CHECKS
    })
    assert doctor.run_doctor(settings)["ready"]
    monkeypatch.delenv("ZS_OFFLINE", raising=False)
    assert doctor.doctor_status(settings)["ready"] is False
    assert doctor.doctor_status(settings)["status"] == "not_started"


def test_credential_change_during_doctor_cannot_restore_stale_ready(tmp_path, monkeypatch):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from zotero_summarizer.api.routes import setup as setup_route
    from zotero_summarizer.services.llm import credentials

    settings = _settings(tmp_path)
    entered, resume, credential_written = Event(), Event(), Event()

    def pause_last_check(_settings):
        entered.set()
        assert resume.wait(5)
        return doctor._row("optional_extras", "ready", "old credential")

    monkeypatch.setattr(doctor, "_RUNNERS", {
        check_id: (pause_last_check if check_id == "optional_extras"
                   else lambda _settings, check_id=check_id: doctor._row(check_id, "ready", "ok"))
        for check_id in doctor._CHECKS
    })
    monkeypatch.setattr(setup_route, "get_settings", lambda: settings)
    def store(name, key):
        credential_written.set()
        return {"name": name, "present": bool(key)}

    monkeypatch.setattr(credentials, "store_api_key", store)
    with ThreadPoolExecutor(max_workers=2) as pool:
        run = pool.submit(doctor.run_doctor, settings)
        assert entered.wait(5)
        saved = pool.submit(lambda: asyncio.run(setup_route.save_ai_credential(
            setup_route.CredentialRequest(name="HOSTED_KEY", api_key="new credential"))))
        assert not credential_written.is_set()  # key write waits for Doctor publication
        resume.set()
        result = run.result(timeout=5)
        saved.result(timeout=5)
    assert result["ready"] is True  # certified old key before replacement
    assert credential_written.is_set()
    assert doctor.doctor_status(settings)["ready"] is False


def test_rss_only_setup_does_not_require_zotero_but_does_require_a_feed(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(doctor, "_RUNNERS", {
        check_id: (lambda _settings, check_id=check_id: doctor._row(check_id,
            "needs_action" if check_id == "zotero" else "ready", "ok"))
        for check_id in doctor._CHECKS
    })
    result = doctor.run_doctor(settings)
    assert result["ready"] is True
    assert next(row for row in result["checks"] if row["id"] == "zotero")["status"] == "needs_action"
    monkeypatch.setitem(doctor._RUNNERS, "rss_source", lambda _: doctor._row("rss_source", "needs_action", "No RSS source"))
    assert doctor.run_doctor(settings)["ready"] is False


def test_doctor_readiness_is_invalidated_when_last_enabled_rss_feed_is_removed(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    with sqlite3.connect(settings.triage_db_path) as conn:
        conn.execute(
            "INSERT INTO rss_feeds (name, url) VALUES ('Feed', 'https://example.test/rss')"
        )
    monkeypatch.setattr(doctor, "_RUNNERS", {
        check_id: (lambda _settings, check_id=check_id: doctor._row(check_id, "ready", "ok"))
        for check_id in doctor._CHECKS
    })
    assert doctor.run_doctor(settings)["ready"]
    with sqlite3.connect(settings.triage_db_path) as conn:
        conn.execute("DELETE FROM rss_feeds")
    assert doctor.doctor_status(settings)["ready"] is False
    assert doctor.doctor_status(settings)["status"] == "not_started"
