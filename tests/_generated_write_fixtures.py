"""Owned native target + queue for generated-note staging, without a live library."""
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests._zotero_fixtures import add_library_item, build_zotero_db
from tests.test_digest_verification import PAPER, _digest
from tests.test_review_source_boundary import _Mapper
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.models import PaperParameters
from zotero_summarizer.services.library import _review_identity, deep_review
from zotero_summarizer.services.setup.bootstrap import _default_goals_config
from zotero_summarizer.services.zotero import generated_notes, zotero
from zotero_summarizer.storage import repositories


def install_generated_writer(tmp_path, monkeypatch):
    monkeypatch.setattr(repositories, "DB_PATH", tmp_path / "triage.db")
    repositories.init_db()
    db = build_zotero_db(tmp_path / "zotero")
    writer = ZoteroWriter(db.parent)
    monkeypatch.setattr(writer, "is_connector_running", lambda: False)
    monkeypatch.setattr(zotero, "get_zotero_writer_or_raise", lambda: writer)
    return db, writer


def base_digest():
    return _digest().model_copy(update={
        "key_findings": ["The evaluation uses held-out data."],
        "methods": "A carefully controlled evaluation.",
        "limitations": "Transfer was not tested.",
        "key_weakness": "One benchmark.",
        "parameters": PaperParameters(dataset="ImageNet", external_validation=False),
    })


@pytest.fixture
def config(monkeypatch):
    value = _default_goals_config()
    monkeypatch.setattr(_review_identity, "state", lambda: SimpleNamespace(app_state=SimpleNamespace(config=value)))
    return value


@pytest.fixture
def paper_io(tmp_path, monkeypatch):
    source = tmp_path / "owned-one-paper.pdf"
    text = (PAPER + " " + "Additional extracted study context uses held-out comparisons. " * 24).strip()
    source.write_text(text, encoding="utf-8")
    reader, extractor = Mock(), Mock()
    reader.get_item_detail.return_value = {
        "title": "Owned paper", "pdf_path": str(tmp_path / "other-attachment.pdf"),
    }
    extractor.extract_text.return_value = text
    layers = Mock(return_value=(None, None, None, None, None))
    stage = Mock(wraps=generated_notes.stage_current_digest)
    monkeypatch.setattr(deep_review._deep_review_layers, "extra_layers", layers)
    monkeypatch.setattr(generated_notes, "stage_current_digest", stage)
    monkeypatch.setattr(deep_review, "now_iso_z", lambda: "2026-10-14T00:00:00Z")
    return SimpleNamespace(source=source, text=text, reader=reader, extractor=extractor,
                           layers=layers, note_stage=stage)


@pytest.fixture
def published_review(config, paper_io, tmp_path, monkeypatch, request):
    from zotero_summarizer.services.library import _review_cache

    key = "LIBR0038"
    db, writer = install_generated_writer(tmp_path, monkeypatch)
    add_library_item(db, item_key=key, title="Owned paper")
    fault = getattr(request, "param", None)
    if fault == "stage_insert_failure":
        with sqlite3.connect(repositories.DB_PATH) as conn:
            conn.execute("""CREATE TRIGGER owned_stage_failure BEFORE INSERT ON pending_changes
                BEGIN SELECT RAISE(ABORT, 'owned stage failure'); END""")
    elif fault is not None:
        raise ValueError("Unknown owned publication fault")
    monkeypatch.setattr(writer, "apply_changes", Mock(wraps=writer.apply_changes))
    monkeypatch.setattr(deep_review, "_try_rebuild_render", Mock())
    monkeypatch.setattr(writer, "remove_items_from_collection", Mock(side_effect=AssertionError("No implicit collection write")))
    seen = []
    stage = generated_notes.stage_current_digest

    def observe_stage(item_key, digest=None):
        seen.append(_review_cache.publication_snapshot(item_key))
        return stage(item_key, digest)

    monkeypatch.setattr(generated_notes, "stage_current_digest", observe_stage)
    config.quality_review.chunk_strategy = "rank"
    generator = Mock()
    generator.pydantic_prompt.return_value = base_digest()
    ctx = {"reader": paper_io.reader, "config": config, "extractor": paper_io.extractor,
           "llm": generator, "llm_map": _Mapper("Generation context only."), "quality_enabled": True}
    deep_review._review_worker({"item_key": key, "pdf_path": str(paper_io.source)}, ctx, "")
    return SimpleNamespace(key=key, writer=writer, db=db, io=paper_io, ctx=ctx, stage_snapshots=seen,
                           entry=_review_cache._read_all(readonly=True)[key], job=deep_review.status(key))
