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
