"""Deep-review cache primitives (split out of deep_review for the LOC cap).

Pins ``copy_review`` — the persistence step that carries an IN-PLACE Today review
(cached under ``stable_feed_key``) onto the new library ``item_key`` when the paper
is materialized into Zotero, so the review shows in the library too.
"""
from __future__ import annotations

import pytest

from test_verification_audit_export import KEY, audit_env, publish  # noqa: F401
from zotero_summarizer.services.library import _review_cache


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(_review_cache, "_cache_path", lambda: tmp_path / "deep_reviews.json")
    yield


def test_copy_review_persists_under_new_key():
    _review_cache._write_one("feed:d:abc", {"quality": {"grade": "A"}})
    assert _review_cache.copy_review("feed:d:abc", "ZNEW") is True
    assert _review_cache.get_cached_review("ZNEW") == {"quality": {"grade": "A"}}
    # The source entry is untouched (copy, not move).
    assert _review_cache.get_cached_review("feed:d:abc") == {"quality": {"grade": "A"}}


def test_copy_review_is_noop_on_absent_or_same_key():
    assert _review_cache.copy_review("missing", "Z") is False       # nothing cached
    _review_cache._write_one("K1", {"quality": {}})
    assert _review_cache.copy_review("K1", "K1") is False           # same key
    assert _review_cache.copy_review("", "Z") is False              # empty src
    assert _review_cache.copy_review("K1", "") is False             # empty dst


def test_cached_review_keys_reports_all():
    _review_cache._write_one("A", {"x": 1})
    _review_cache._write_one("B", {"x": 2})
    assert _review_cache.cached_review_keys() == {"A", "B"}


def test_corrupt_cache_is_quarantined_without_overwriting_prior_recovery(tmp_path):
    path = _review_cache._cache_path()
    corrupt = b'{"reviews":'
    path.write_bytes(corrupt)

    assert _review_cache.cached_review_keys() == set()
    assert not path.exists()
    assert (tmp_path / "deep_reviews.json.corrupt").read_bytes() == corrupt

    path.write_text("not json", encoding="utf-8")
    assert _review_cache.current_reviews() == {}
    assert (tmp_path / "deep_reviews.json.corrupt-1").read_text(encoding="utf-8") == "not json"


def test_current_review_requires_matching_source_and_generation_identity(audit_env):
    raw, client, source = publish(audit_env)
    current = _review_cache.get_cached_review(KEY)
    _review_cache._write_one('LEGACY', {'digest': {}})
    before = _review_cache._cache_path().read_bytes()

    assert _review_cache.get_current_review(KEY) == current
    assert _review_cache.get_current_review('LEGACY') is None
    assert _review_cache.current_review_keys() == {KEY}
    assert raw['verification_audit'] is not None

    original = source.read_bytes()
    source.write_bytes(b'A different owned source')
    assert _review_cache.get_current_review(KEY) is None
    assert _review_cache.current_review_keys() == set()
    source.write_bytes(original)
    assert _review_cache.get_current_review(KEY) == current

    goals = list(audit_env.config.research_goals)
    audit_env.config.research_goals = [*goals, 'Changed generation goal']
    assert _review_cache.get_current_review(KEY) is None
    assert _review_cache.current_review_keys() == set()
    audit_env.config.research_goals = goals
    assert _review_cache.get_current_review(KEY) == current
    assert _review_cache._cache_path().read_bytes() == before
    assert (client.generated, client.calls) == (1, 1)


@pytest.mark.parametrize('raw', [b'{', b'{"reviews": []}', b'\xff'])
def test_readonly_cache_reader_preserves_malformed_original(raw, tmp_path):
    path = _review_cache._cache_path()
    path.write_bytes(raw)
    with pytest.raises(ValueError, match='Invalid deep-review cache'):
        _review_cache._read_all(readonly=True)
    assert path.read_bytes() == raw
    assert sorted(p.name for p in tmp_path.iterdir()) == ['deep_reviews.json']


def test_explicit_audit_export_missing_review_never_creates_a_store(tmp_path):
    from zotero_summarizer.api.errors import APIError
    with pytest.raises(APIError) as error:
        _review_cache.export_review_audit('ABCDEFGH')
    assert error.value.status_code == 404
    assert not _review_cache._cache_path().exists()
