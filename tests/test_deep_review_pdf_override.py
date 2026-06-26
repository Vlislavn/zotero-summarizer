"""``_review_one`` honors an injected ``item['pdf_path']`` (the fleet's acquired cache
download) OVER the Zotero attachment, so a verdict works without a Zotero write — and
falls back to the attachment when no override is given.
"""
from __future__ import annotations

import types

from zotero_summarizer.services.library import deep_review


def _config():
    qr = types.SimpleNamespace(lean_max_text_chars=12_000, max_text_chars=60_000)
    return types.SimpleNamespace(quality_review=qr, research_goals=[])


def _wire(monkeypatch, *, detail):
    reader = types.SimpleNamespace(get_item_detail=lambda k: detail)
    seen = {"extracted": []}
    extractor = types.SimpleNamespace(
        extract_text=lambda path: (seen["extracted"].append(path), "BODY TEXT")[1]
    )
    digest = types.SimpleNamespace(model_dump=lambda: {"grade": "A", "read_decision": "read"})
    monkeypatch.setattr(deep_review.quality_review, "assess_digest", lambda **_k: digest)
    monkeypatch.setattr(deep_review._deep_review_layers, "extra_layers",
                        lambda ctx: ({"quality_band": "ok"}, [], {"type": "x"}, None))
    # The note write is a local import inside _review_one — patch the source symbol.
    from zotero_summarizer.services.zotero import zotero as zsvc
    monkeypatch.setattr(zsvc, "zotero_upsert_digest_note", lambda *_a, **_k: None)
    return reader, extractor, seen


def test_injected_pdf_path_overrides_missing_zotero_attachment(monkeypatch):
    """No local Zotero PDF, but the fleet injected a cache path → review runs from it."""
    detail = {"title": "T", "pdf_path": "", "item_type": "journalArticle"}
    reader, extractor, seen = _wire(monkeypatch, detail=detail)

    entry = deep_review._review_one(
        {"item_key": "K1", "title": "T", "pdf_path": "/tmp/cache/k1.pdf"},
        reader=reader, config=_config(), extractor=extractor, llm=object(), quality_enabled=True,
    )
    assert entry["needs_pdf"] is False
    assert entry["digest"] == {"grade": "A", "read_decision": "read"}
    assert seen["extracted"] == ["/tmp/cache/k1.pdf"]  # extracted from the INJECTED path


def test_no_override_falls_back_to_zotero_attachment(monkeypatch):
    """Without an override, the Zotero ``detail['pdf_path']`` is used as before."""
    detail = {"title": "T", "pdf_path": "/zotero/storage/k1.pdf", "item_type": "journalArticle"}
    reader, extractor, seen = _wire(monkeypatch, detail=detail)

    entry = deep_review._review_one(
        {"item_key": "K1", "title": "T"},  # no pdf_path injected
        reader=reader, config=_config(), extractor=extractor, llm=object(), quality_enabled=True,
    )
    assert entry["needs_pdf"] is False
    assert seen["extracted"] == ["/zotero/storage/k1.pdf"]


def test_no_pdf_anywhere_marks_needs_pdf(monkeypatch):
    """Neither an injected path nor a Zotero attachment → honest needs_pdf, no extract."""
    detail = {"title": "T", "pdf_path": "", "item_type": "journalArticle"}
    reader, extractor, seen = _wire(monkeypatch, detail=detail)

    entry = deep_review._review_one(
        {"item_key": "K1", "title": "T"},
        reader=reader, config=_config(), extractor=extractor, llm=object(), quality_enabled=True,
    )
    assert entry["needs_pdf"] is True and entry["digest"] is None
    assert seen["extracted"] == []  # never tried to extract
