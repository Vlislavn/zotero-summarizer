"""Durable identifier unions and conservative native-lookup machine contracts."""
import sqlite3
from types import SimpleNamespace

import pytest

from zotero_summarizer.integrations._zotero_read_lookup import ZoteroLookupMixin
from zotero_summarizer.services.search._models import Candidate
from zotero_summarizer.services.search.dedup import to_version_families
from zotero_summarizer.services.search.pipeline import _annotate_library_coverage


@pytest.mark.parametrize("invalid", [None, "doi:10.1234/a", [1], [""], ["title:Same"], ["doi:"]])
def test_malformed_family_identifiers_fail_at_the_input_boundary(invalid):
    with pytest.raises(ValueError):
        Candidate.from_dict({"title": "Malformed", "family_identifier_keys": invalid})


def test_family_keys_normalize_and_roundtrip_without_changing_the_address():
    candidate = Candidate(title="Public metadata", doi="10.1234/current",
                          family_identifier_keys=["doi:https://doi.org/10.1234/other",
                                                  "arxiv:2401.01234v3", "doi:10.1234/other"])
    restored = Candidate.from_dict(candidate.to_dict())
    assert restored.candidate_id == candidate.candidate_id
    assert restored.family_identifier_keys == ["doi:10.1234/other", "arxiv:2401.01234"]
    assert restored.identifier_keys() == ["doi:10.1234/current", "doi:10.1234/other", "arxiv:2401.01234"]


@pytest.mark.parametrize("reverse", [False, True])
def test_transitive_ids_survive_json_then_another_richer_refinement(reverse):
    members = [Candidate(title="A", doi="10.1234/a", arxiv_id="2401.01234"),
               Candidate(title="B", arxiv_id="2401.01234", pmid="123456"),
               Candidate(title="C", doi="10.1234/c", pmid="123456", abstract="Rich metadata", venue="Journal")]
    if reverse:
        members.reverse()
    address = members[0].candidate_id
    expected = {key for member in members for key in member.identifier_keys()}
    family = to_version_families(members)[0]
    assert set(family.identifier_keys()) == expected
    restored = Candidate.from_dict(family.to_dict())
    delta = Candidate(title="Richer A", doi="10.1234/a", arxiv_id="2402.05678",
                      abstract="More detailed public metadata than the earlier journal record", venue="Journal")
    joined = to_version_families([restored, delta])
    assert len(joined) == 1 and joined[0].candidate_id == address
    assert set(joined[0].identifier_keys()) == expected | {"arxiv:2402.05678"}


@pytest.mark.parametrize("preferred,alias,expected,key", [
    (None, "HITKEY01", True, "HITKEY01"),
    (OSError("Synthetic failure"), "HITKEY01", True, "HITKEY01"),
    (None, OSError("Synthetic failure"), None, None),
    (None, None, False, None),
    ("PRIMARY1", OSError("Must not query after a hit"), True, "PRIMARY1"),
])
def test_fresh_coverage_uses_all_ids_without_turning_partial_failure_into_absence(preferred, alias, expected, key):
    candidate = Candidate(title="Family", doi="10.1234/preferred",
                          family_identifier_keys=["doi:10.1234/alias"],
                          in_library=True, existing_zotero_key="STALE000")
    calls = []

    def lookup(*, doi=None, arxiv_id=None):
        calls.append((doi, arxiv_id))
        value = preferred if doi == "10.1234/preferred" else alias
        if isinstance(value, Exception):
            raise value
        return value

    _annotate_library_coverage([candidate], SimpleNamespace(find_by_external_id=lookup))
    assert candidate.in_library is expected and candidate.existing_zotero_key == key
    assert len(calls) == (1 if preferred == "PRIMARY1" else 2)


def test_genuine_positive_key_is_captured_before_the_preferred_record_mutates():
    saved = Candidate(title="Saved", doi="10.1234/a", arxiv_id="2401.01234",
                      in_library=True, existing_zotero_key="SAVED001")
    richer = Candidate(title="Rich", doi="10.1234/b", arxiv_id="2401.01234", abstract="Public abstract",
                       in_library=False, existing_zotero_key="STALE000")
    result = to_version_families([richer, saved])[0]
    assert result.title == "Rich" and result.in_library is True
    assert result.existing_zotero_key == "SAVED001"


def test_unsupported_identifiers_do_not_trigger_a_native_read():
    def forbidden(**kwargs):
        raise AssertionError("PMID is not a supported native DOI/arXiv lookup")

    candidate = Candidate(title="PMID only", pmid="123456")
    _annotate_library_coverage([candidate], SimpleNamespace(find_by_external_id=forbidden))
    assert candidate.in_library is None and candidate.existing_zotero_key is None


@pytest.fixture
def native_reader():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE libraries(libraryID INTEGER, type TEXT);
        INSERT INTO libraries VALUES (1, 'user');
        CREATE TABLE items(itemID INTEGER, libraryID INTEGER, key TEXT);
        INSERT INTO items VALUES (1, 1, 'UNRELATED'), (2, 1, 'SAFEKEY1'), (3, 1, 'ARXIV001');
        CREATE TABLE itemData(itemID INTEGER, fieldID INTEGER, valueID INTEGER);
        INSERT INTO itemData VALUES (1, 1, 1), (2, 2, 2), (3, 1, 3);
        CREATE TABLE fields(fieldID INTEGER, fieldName TEXT);
        INSERT INTO fields VALUES (1, 'url'), (2, 'DOI');
        CREATE TABLE itemDataValues(valueID INTEGER, value TEXT);
        INSERT INTO itemDataValues VALUES
            (1, 'https://example.org/unrelated'), (2, '10.1234/saved'),
            (3, 'https://arxiv.org/abs/2401.01234v3');
        CREATE TABLE deletedItems(itemID INTEGER);
    """)

    class Reader(ZoteroLookupMixin):
        def _execute_read(self, read):
            return read(connection)

    yield Reader()
    connection.close()


@pytest.mark.parametrize("unsafe", ["%", "_", "2401.%", "2401.0123_"])
def test_retained_arxiv_wildcard_cannot_claim_an_unrelated_saved_key(native_reader, unsafe):
    sparse = Candidate(title="Sparse", doi="10.1234/missing", arxiv_id=unsafe)
    richer = Candidate(title="Rich", doi="10.1234/missing", arxiv_id="2403.07890",
                       abstract="Richer public metadata")
    family = Candidate.from_dict(to_version_families([sparse, richer])[0].to_dict())
    assert family.arxiv_id == "2403.07890" and f"arxiv:{unsafe}" in family.identifier_keys()

    _annotate_library_coverage([family], native_reader)

    assert family.in_library is None and family.existing_zotero_key is None


@pytest.mark.parametrize("unsafe", ["%", "_"])
@pytest.mark.parametrize("doi,expected,key", [
    ("10.1234/saved", True, "SAFEKEY1"),
    ("10.1234/missing", None, None),
])
def test_unsafe_primary_arxiv_still_checks_safe_doi(native_reader, unsafe, doi, expected, key):
    candidate = Candidate(title="Paired identifiers", doi=doi, arxiv_id=unsafe)

    _annotate_library_coverage([candidate], native_reader)

    assert candidate.in_library is expected and candidate.existing_zotero_key == key


@pytest.mark.parametrize("alias,expected,key", [
    ("2401.01234", True, "ARXIV001"),
    ("2402.05678", False, None),
])
def test_native_coverage_keeps_ordinary_retained_arxiv_hit_and_miss(native_reader, alias, expected, key):
    candidate = Candidate(title="Ordinary alias", doi="10.1234/missing", arxiv_id="2403.07890",
                          family_identifier_keys=[f"arxiv:{alias}"])

    _annotate_library_coverage([candidate], native_reader)

    assert candidate.in_library is expected and candidate.existing_zotero_key == key


@pytest.mark.parametrize("unsafe", ["%", "_"])
def test_genuine_retained_doi_hit_wins_after_unsafe_arxiv(native_reader, unsafe):
    candidate = Candidate(title="Later genuine hit", doi="10.1234/missing",
                          family_identifier_keys=[f"arxiv:{unsafe}", "doi:10.1234/saved"])

    _annotate_library_coverage([candidate], native_reader)

    assert candidate.in_library is True and candidate.existing_zotero_key == "SAFEKEY1"
