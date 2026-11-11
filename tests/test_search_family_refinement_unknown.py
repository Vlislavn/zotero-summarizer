"""Unknown new family observations cannot borrow a persisted absence verdict."""
import pytest

from tests.test_search_refinement_coverage import _Reader, _round
from zotero_summarizer.services.search import session
from zotero_summarizer.services.search._models import Candidate


@pytest.mark.parametrize("namespace", ["doi", "arxiv"])
@pytest.mark.parametrize("prefer_new", [False, True])
@pytest.mark.parametrize("result,expected,key", [
    (OSError("Synthetic unavailable native reader"), None, None),
    (None, False, None),
    ("SAVED001", True, "SAVED001"),
])
def test_persisted_refinement_keeps_new_identity_failure_unknown(
    monkeypatch, tmp_path, namespace, prefer_new, result, expected, key,
):
    if namespace == "doi":
        old = Candidate(title="Old", doi="10.1234/old", arxiv_id="2401.01234", in_library=False)
        new = Candidate(title="New", doi="10.1234/new", arxiv_id="2401.01234")
    else:
        old = Candidate(title="Old", doi="10.1234/shared", arxiv_id="2401.01234", in_library=False)
        new = Candidate(title="New", doi="10.1234/shared", arxiv_id="2402.05678")
    preferred = new if prefer_new else old
    preferred.abstract, preferred.venue = "Richer public metadata", "Journal"
    old.query_score, new.query_score = 0.9, 0.7
    old.review = {"state": "reviewed", "brief": {"tldr": "Retain existing review"}}
    address = old.candidate_id
    identities = set(old.identifier_keys()) | set(new.identifier_keys())
    reader = _Reader(result)

    output = _round(monkeypatch, tmp_path, [old], new, reader)
    restored = session.load(output.id).candidates

    assert len(restored) == 1 and restored[0].candidate_id == address
    assert set(restored[0].identifier_keys()) == identities
    assert restored[0].in_library is expected
    assert restored[0].existing_zotero_key == key
    assert restored[0].review == old.review
    assert reader.calls == [(new.doi or None, new.arxiv_id or None)]
