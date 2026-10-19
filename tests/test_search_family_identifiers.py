"""Saved version-family membership retains literal identities, not title guesses."""
from types import SimpleNamespace

import pytest

from zotero_summarizer.services.search._models import Candidate
from zotero_summarizer.services.search.dedup import to_version_families
from zotero_summarizer.services.search.pipeline import _annotate_library_coverage


@pytest.mark.parametrize("namespace", ["doi", "arxiv"])
def test_saved_alternate_identifier_survives_preferred_version_and_coverage(namespace):
    if namespace == "doi":
        first = Candidate(title="Preprint", doi="10.1234/saved", arxiv_id="2401.01234")
        richer = Candidate(title="Journal", doi="10.1234/journal", arxiv_id="2401.01234",
                           abstract="Richer public metadata", venue="Journal")
        saved = {"doi": "10.1234/saved"}
    else:
        first = Candidate(title="First version", doi="10.1234/shared", arxiv_id="2401.01234")
        richer = Candidate(title="Later version", doi="10.1234/shared", arxiv_id="2402.05678",
                           abstract="Richer public metadata", venue="Journal")
        saved = {"arxiv_id": "2401.01234"}
    address = first.candidate_id
    families = to_version_families([first, richer])
    assert len(families) == 1 and families[0].candidate_id == address
    assert families[0].title == richer.title
    calls = []

    def find_by_external_id(**identifiers):
        calls.append(identifiers)
        return "SAVED001" if all(identifiers.get(key) == value for key, value in saved.items()) else None

    _annotate_library_coverage(families, SimpleNamespace(find_by_external_id=find_by_external_id))

    assert families[0].in_library is True, (families[0].to_dict(), calls)
    assert families[0].existing_zotero_key == "SAVED001"



def test_same_title_is_not_a_family_identity():
    rows = [Candidate(title="Same title", doi="10.1234/first"),
            Candidate(title="Same title", doi="10.1234/second")]
    assert len(to_version_families(rows)) == 2



def test_failed_membership_lookup_is_not_a_known_miss():
    def unavailable(**identifiers):
        raise OSError("Synthetic unavailable reader")

    rows = [Candidate(title="Unknown", doi="10.1234/unknown")]
    _annotate_library_coverage(rows, SimpleNamespace(find_by_external_id=unavailable))
    assert rows[0].in_library is None and rows[0].existing_zotero_key is None
