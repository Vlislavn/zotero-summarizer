"""Live admission preserves candidate literals; legacy decoding stays compatible."""
from unittest.mock import Mock

import pytest

from test_method_source_admission import MEMBER, config, payload, receipt_path, refine, rows
from zotero_summarizer.models import MethodAndCode, SummarizeRequest
from zotero_summarizer.services.triage import _method_admission, summarization


@pytest.mark.parametrize("padding", [" ", "\t", "\n", "\u00a0"])
@pytest.mark.parametrize("side", ["left", "right"])
@pytest.mark.parametrize("narrative", [False, True])
@pytest.mark.parametrize("repair", [False, True], ids=["first", "repair"])
def test_padded_candidates_are_receipted_not_normalized(config, receipt_path, padding, side, narrative, repair):
    candidate = padding + MEMBER if side == "left" else MEMBER + padding
    output = payload([MEMBER, candidate, MEMBER])
    if narrative:
        output = "Analysis follows: " + output + " End."
    client = Mock()
    client.prompt.side_effect = (["{malformed"] if repair else []) + [output]

    result = refine(client, config)

    assert result.method_and_code.artifacts == [MEMBER, MEMBER]
    record, = rows(receipt_path)
    assert record["candidate"] == candidate
    assert record["field"] == "method_and_code.artifacts[1]"
    assert record["phase"] == ("repair" if repair else "first")
    assert client.prompt.call_count == (2 if repair else 1)


@pytest.mark.parametrize("repair", [False, True], ids=["first", "repair"])
def test_dry_padded_candidate_rejects_before_state_or_receipt_io(monkeypatch, config, repair):
    forbidden = Mock(side_effect=AssertionError("Dry rejection cannot access state or receipts"))
    monkeypatch.setattr(_method_admission, "settings", forbidden)
    monkeypatch.setattr(_method_admission.run_log, "append_run", forbidden)
    monkeypatch.setattr(summarization, "state", forbidden)
    client = Mock()
    client.prompt.side_effect = (["{malformed"] if repair else []) + [payload([" " + MEMBER])]

    with pytest.raises(ValueError, match="Write-free refinement rejected"):
        summarization.run_abstract_dry(config, SummarizeRequest(title="Fixture", abstract=MEMBER), client)

    assert client.prompt.call_count == (2 if repair else 1)
    client.pydantic_prompt.assert_not_called()
    forbidden.assert_not_called()


def test_legacy_model_decoding_keeps_existing_whitespace_cleanup():
    assert MethodAndCode.model_validate({"artifacts": [" " + MEMBER + " ", " "]}).artifacts == [MEMBER]
