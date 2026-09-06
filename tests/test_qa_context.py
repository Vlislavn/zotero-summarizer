from zotero_summarizer.services.library import qa_context


def test_evidence_handle_is_stable_and_invalidates_after_reparse() -> None:
    artifact = {"pdf_key": "v1", "full_text": "Before. Exact supporting quote here. After."}
    handle = qa_context.evidence_handle("P1", artifact, "question", "Exact supporting quote here.")

    assert qa_context.verified_quote("P1", artifact, handle) == "Exact supporting quote here."
    assert handle == qa_context.evidence_handle(
        "P1", artifact, "question", "Exact supporting quote here.",
    )
    reparsed = {**artifact, "pdf_key": "v2"}
    assert qa_context.verified_quote("P1", reparsed, handle) is None
    assert qa_context.citation("P1", reparsed, handle, answered=True)["location_verified"] is False
    assert qa_context.citation("P1", reparsed, handle, answered=True)["quote_verified"] is False


def test_unlocated_quote_cannot_claim_verification() -> None:
    artifact = {"pdf_key": "v1", "full_text": "Actual paper text."}
    handle = qa_context.evidence_handle("P1", artifact, "question", "Invented review evidence.")

    citation = qa_context.citation("P1", artifact, handle, answered=True)

    assert citation["claimed"] is True
    assert citation["quote_verified"] is False
    assert citation["location_verified"] is False


def test_conversation_quotes_are_not_labelled_verified() -> None:
    history = [{"question": "Which dataset?", "answer": "InventedSet", "quote": "Invented evidence."}]

    text, _, _ = qa_context.compact_history("P1", {"full_text": "Actual paper."}, history)

    assert "Invented evidence." in text
    assert "Verified quote:" not in text
