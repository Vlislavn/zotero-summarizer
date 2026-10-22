"""HTTP preservation contracts for durable paper-question submissions."""
from contextlib import closing
import sqlite3
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from zotero_summarizer.api.errors import APIError, install_error_handlers
from zotero_summarizer.api.routes import library
from zotero_summarizer.services.library import paper_render
from zotero_summarizer.runtime import AppContext, set_context
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage.migrations import TRIAGE_MIGRATIONS, run_migrations


def _app():
    app = FastAPI()
    app.include_router(library.router)
    install_error_handlers(app)
    return app


def _database(tmp_path):
    settings = Settings.load(project_root=tmp_path)
    run_migrations(settings.triage_db_path, "triage", TRIAGE_MIGRATIONS)
    set_context(AppContext(settings=settings))
    return settings


def _questions(database):
    with closing(sqlite3.connect(database)) as conn:
        rows = conn.execute(
            "SELECT submission_id, item_key, question, origin, preset_id, submitted_at "
            "FROM review_questions ORDER BY submission_id"
        ).fetchall()
    return rows


def _ask_payload(submission_id, **changes):
    return {
        "item_key": "P1",
        "question": "Is there external validation?",
        "submission_id": str(submission_id),
        "origin": "preset",
        "preset_id": "external_validation",
        **changes,
    }


def test_ask_commits_before_qa_and_retry_deduplicates_only_the_comment(tmp_path, monkeypatch):
    settings = _database(tmp_path)
    submission_id = uuid4()
    calls = []

    def qa_after_commit(item_key, question, **kwargs):
        with closing(sqlite3.connect(settings.triage_db_path)) as conn:
            row = conn.execute(
                "SELECT submission_id, item_key, question, origin, preset_id "
                "FROM review_questions WHERE submission_id = ?",
                (str(submission_id),),
            ).fetchone()
        assert row == (
            str(submission_id), "P1", question, "preset", "external_validation"
        )
        calls.append((item_key, question, kwargs))
        return {
            "answer": None,
            "abstained": True,
            "mode": "retrieval",
            "citation": {"claimed": False},
        }

    monkeypatch.setattr(library.qa, "ask_paper", qa_after_commit)
    payload = _ask_payload(
        submission_id,
        mode="retrieval",
        history=[{"question": "Earlier?", "answer": "Earlier answer"}],
    )

    with TestClient(_app()) as client:
        first = client.post("/api/library/ask", json=payload)
        retry = client.post("/api/library/ask", json=payload)

    assert first.status_code == retry.status_code == 200
    for response in (first, retry):
        body = response.json()
        assert body["answer"] is None and body["abstained"] is True
        assert body["mode"] == "retrieval"
        assert body["citation"] == {"claimed": False}
        assert body["question_saved"] is True
        assert body["question_submission"]["submission_id"] == str(submission_id)
        assert body["question_submission"]["origin"] == "preset"
    assert len(calls) == 2  # Online retries may rerun QA; the comment remains unique.
    assert calls[0][2]["mode"] == "retrieval"
    assert calls[0][2]["history"][0]["question"] == "Earlier?"
    assert len(_questions(settings.triage_db_path)) == 1


def test_legacy_ask_generates_submission_uuid_and_defaults_to_typed(tmp_path, monkeypatch):
    settings = _database(tmp_path)
    monkeypatch.setattr(
        library.qa, "ask_paper", lambda *_args, **_kwargs: {"answer": "Supported"}
    )

    with TestClient(_app()) as client:
        response = client.post(
            "/api/library/ask",
            json={"item_key": "P1", "question": "What is the main result?"},
        )

    assert response.status_code == 200, response.text
    submission = response.json()["question_submission"]
    assert UUID(submission["submission_id"])
    assert submission["origin"] == "typed"
    assert submission["preset_id"] is None
    assert response.json()["question_saved"] is True
    assert len(_questions(settings.triage_db_path)) == 1


def test_comment_only_save_replay_and_read_never_invoke_qa(tmp_path, monkeypatch):
    settings = _database(tmp_path)
    submission_id = uuid4()
    monkeypatch.setattr(
        library.qa, "ask_paper",
        lambda *_args, **_kwargs: pytest.fail("comment-only save must not run QA"),
    )
    payload = {
        "submission_id": str(submission_id),
        "item_key": "P1",
        "question": "What is the main result?",
        "origin": "typed",
    }

    with TestClient(_app()) as client:
        first = client.post("/api/library/questions", json=payload)
        retry = client.post("/api/library/questions", json=payload)
        listed = client.get("/api/library/questions", params={"item_key": "P1"})

    assert first.status_code == retry.status_code == listed.status_code == 200
    first_row = first.json()["question_submission"]
    assert retry.json()["question_submission"] == first_row
    assert first.json()["question_saved"] is True
    assert listed.json() == {"item_key": "P1", "questions": [first_row]}
    assert len(_questions(settings.triage_db_path)) == 1


def test_lexically_valid_key_saves_before_renderer_symlink_rejection(tmp_path, monkeypatch):
    settings = _database(tmp_path)
    settings.paper_render_dir.mkdir(parents=True, exist_ok=True)
    (settings.paper_render_dir / "LINK").symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setattr(
        paper_render, "_pdf_for_item_or_acquire",
        lambda *_args, **_kwargs: pytest.fail("symlink key reached paper acquisition"),
    )
    payload = _ask_payload(uuid4(), item_key="LINK")

    with TestClient(_app()) as client:
        failed_qa = client.post("/api/library/ask", json=payload)
        saved_only = client.post("/api/library/questions", json=payload)

    assert failed_qa.status_code == 422
    assert failed_qa.json()["error"] == "validation_error"
    receipt = failed_qa.json()["details"]
    assert receipt["question_saved"] is True
    assert receipt["question_submission"]["item_key"] == "LINK"
    assert saved_only.status_code == 200
    assert saved_only.json() == receipt
    assert len(_questions(settings.triage_db_path)) == 1


def test_comment_only_endpoint_requires_submission_id(tmp_path, monkeypatch):
    settings = _database(tmp_path)
    monkeypatch.setattr(
        library.qa, "ask_paper", lambda *_args, **_kwargs: pytest.fail("QA was called")
    )

    with TestClient(_app()) as client:
        response = client.post(
            "/api/library/questions",
            json={"item_key": "P1", "question": "What is the main result?"},
        )

    assert response.status_code == 422
    assert _questions(settings.triage_db_path) == []


def test_changed_payload_for_existing_uuid_conflicts_before_another_qa_call(
    tmp_path, monkeypatch
):
    settings = _database(tmp_path)
    submission_id = uuid4()
    calls = []
    monkeypatch.setattr(
        library.qa, "ask_paper",
        lambda *_args, **_kwargs: calls.append(True) or {"answer": "A"},
    )

    with TestClient(_app()) as client:
        first = client.post("/api/library/ask", json=_ask_payload(submission_id))
        conflict = client.post(
            "/api/library/ask",
            json=_ask_payload(submission_id, question="Changed question"),
        )

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "submission_conflict"
    assert calls == [True]
    assert len(_questions(settings.triage_db_path)) == 1


def test_new_uuid_preserves_an_intentional_repeat(tmp_path):
    settings = _database(tmp_path)
    base = {"item_key": "P1", "question": "What is the main result?", "origin": "typed"}

    with TestClient(_app()) as client:
        first = client.post(
            "/api/library/questions", json={**base, "submission_id": str(uuid4())}
        )
        second = client.post(
            "/api/library/questions", json={**base, "submission_id": str(uuid4())}
        )
        listed = client.get("/api/library/questions", params={"item_key": "P1"})

    assert first.status_code == second.status_code == listed.status_code == 200
    assert len(listed.json()["questions"]) == 2
    assert len({row[0] for row in _questions(settings.triage_db_path)}) == 2


def test_questions_survive_context_restart_and_read_through_alias(tmp_path):
    settings = _database(tmp_path)
    submission_id = uuid4()
    payload = {
        "submission_id": str(submission_id),
        "item_key": "feed:42",
        "question": "What is the main result?",
        "origin": "typed",
    }

    with TestClient(_app()) as client:
        saved = client.post("/api/library/questions", json=payload)
    assert saved.status_code == 200

    with closing(sqlite3.connect(settings.triage_db_path)) as conn:
        conn.execute(
            "INSERT INTO feed_key_aliases(old_key, stable_feed_key) VALUES (?, ?)",
            ("feed:42", "feed:guid:stable"),
        )
        conn.commit()

    set_context(AppContext(settings=Settings.load(project_root=tmp_path)))
    with TestClient(_app()) as client:
        response = client.get(
            "/api/library/questions", params={"item_key": "feed:guid:stable"}
        )
        retry = client.post("/api/library/questions", json=payload)
        changed_key = client.post(
            "/api/library/questions", json={**payload, "item_key": "feed:guid:stable"}
        )

    assert response.status_code == retry.status_code == 200
    assert retry.json()["question_submission"] == saved.json()["question_submission"]
    assert changed_key.status_code == 409
    assert len(_questions(settings.triage_db_path)) == 1
    row = response.json()["questions"][0]
    assert row["submission_id"] == str(submission_id)
    assert row["item_key"] == "feed:42"  # Reads resolve aliases without rewriting identity.


@pytest.mark.parametrize("endpoint", ["/api/library/ask", "/api/library/questions"])
@pytest.mark.parametrize("item_key", ["", " \t\n", ".", "..", "../bad", "a/b", "a\\b", "a\0b", "/absolute"])
def test_invalid_key_never_persists_or_starts_qa(tmp_path, monkeypatch, endpoint, item_key):
    settings = _database(tmp_path)
    called = []

    def actual_key_guard(key, *_args, **_kwargs):
        called.append(key)
        paper_render._state_path(key, root=tmp_path / "render")
        return {"answer": "Fixture answer"}

    monkeypatch.setattr(library.qa, "ask_paper", actual_key_guard)
    payload = {
        "submission_id": str(uuid4()), "item_key": item_key,
        "question": "Fixture question", "origin": "typed",
    }
    with TestClient(_app()) as client:
        response = client.post(endpoint, json=payload)

    assert response.status_code == 422, response.json()
    assert called == []
    assert _questions(settings.triage_db_path) == []


@pytest.mark.parametrize(
    "payload",
    [
        {"item_key": "P1", "question": "  \n "},
        {"item_key": "P1", "question": "A question?", "origin": "preset"},
        {"item_key": "P1", "question": "A question?", "submission_id": "not-a-uuid"},
    ],
)
def test_invalid_question_submission_is_rejected_without_qa(tmp_path, monkeypatch, payload):
    settings = _database(tmp_path)
    monkeypatch.setattr(
        library.qa, "ask_paper", lambda *_args, **_kwargs: pytest.fail("QA was called")
    )

    with TestClient(_app()) as client:
        response = client.post("/api/library/ask", json=payload)

    assert response.status_code == 422
    assert _questions(settings.triage_db_path) == []


def test_database_save_failure_prevents_qa(tmp_path, monkeypatch):
    settings = _database(tmp_path)
    calls = []
    real_connect = sqlite3.connect

    def fail_app_database(path, *args, **kwargs):
        if str(path) == str(settings.triage_db_path):
            raise sqlite3.OperationalError("injected question storage failure")
        return real_connect(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", fail_app_database)
    monkeypatch.setattr(library.qa, "ask_paper", lambda *args, **kwargs: calls.append(args))

    with TestClient(_app(), raise_server_exceptions=False) as client:
        response = client.post(
            "/api/library/ask", json=_ask_payload(uuid4())
        )

    assert response.status_code >= 500
    assert calls == []


def test_qa_failure_is_http_error_with_durable_submission_receipt(tmp_path, monkeypatch):
    settings = _database(tmp_path)
    submission_id = uuid4()

    def fail_qa(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(library.qa, "ask_paper", fail_qa)
    with TestClient(_app(), raise_server_exceptions=False) as client:
        response = client.post("/api/library/ask", json=_ask_payload(submission_id))

    assert response.status_code == 502
    body = response.json()
    assert body["error"] == "qa_failed"
    assert body["details"]["question_saved"] is True
    assert body["details"]["question_submission"]["submission_id"] == str(submission_id)
    assert len(_questions(settings.triage_db_path)) == 1


def test_qa_api_error_preserves_reason_and_adds_submission_receipt(tmp_path, monkeypatch):
    _database(tmp_path)
    submission_id = uuid4()

    def qa_error(*_args, **_kwargs):
        raise APIError(
            error="needs_pdf", message="No PDF available", status_code=404,
            details={"source": "paper-reader"},
        )

    monkeypatch.setattr(library.qa, "ask_paper", qa_error)
    with TestClient(_app(), raise_server_exceptions=False) as client:
        response = client.post("/api/library/ask", json=_ask_payload(submission_id))

    assert response.status_code == 404
    assert response.json()["error"] == "needs_pdf"
    assert response.json()["details"]["source"] == "paper-reader"
    assert response.json()["details"]["question_saved"] is True
    assert response.json()["details"]["question_submission"]["submission_id"] == str(
        submission_id
    )


def test_unexpected_null_qa_result_is_not_reported_as_success(tmp_path, monkeypatch):
    _database(tmp_path)
    submission_id = uuid4()
    monkeypatch.setattr(library.qa, "ask_paper", lambda *_args, **_kwargs: None)

    with TestClient(_app(), raise_server_exceptions=False) as client:
        response = client.post("/api/library/ask", json=_ask_payload(submission_id))

    assert response.status_code >= 500
    assert response.json()["details"]["question_saved"] is True
    assert response.json()["details"]["question_submission"]["submission_id"] == str(
        submission_id
    )
