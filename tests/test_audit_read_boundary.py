"""No native filesystem mutation and Python3.10 hashing API regression controls."""
import hashlib
import os
import sqlite3

import pytest

from test_verification_audit_export import audit_env, _publish_attached, get, publish  # noqa: F401
from zotero_summarizer.services.library import _review_cache


@pytest.mark.parametrize('changed_attachment', [False, True])
def test_export_does_not_open_native_wal_or_initialize_its_shared_memory(audit_env, monkeypatch, changed_attachment):
    reader, db, original = _publish_attached(audit_env, monkeypatch)
    with sqlite3.connect(db) as writer:
        assert writer.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
        writer.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        if changed_attachment:
            replacement = audit_env.root / 'replacement.pdf'
            replacement.write_bytes(b'New committed attachment; old source survives')
            writer.execute('UPDATE itemAttachments SET path=?', (str(replacement),))
        else:
            writer.execute('UPDATE items SET version=version+1')
        writer.commit()
        native = audit_env.root / 'owned-native-wal'
        native.mkdir()
        for suffix in ('', '-wal'):
            (native / ('zotero.sqlite' + suffix)).write_bytes(db.with_name(db.name + suffix).read_bytes())
    writer.close()
    reader.db_path = native / 'zotero.sqlite'
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in native.iterdir()}
    cache_before = _review_cache._cache_path().read_bytes()
    calls = []
    connect = sqlite3.connect
    def record_connect(*args, **kwargs):
        calls.append((args, kwargs))
        return connect(*args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', record_connect)

    response = get(audit_env, '/api/library/deep-review/ABCDEFGH/audit')

    assert response.status_code == 200
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in native.iterdir()}
    assert after == before
    assert calls == []
    payload = response.json()
    assert payload['audit_status'] == 'unavailable'
    assert payload['verification_audit'] is None
    assert payload['retained_evidence']['scope'] == 'retained_source_only'
    assert payload['retained_evidence']['current_item_binding'] == 'not_established'
    assert payload['retained_evidence']['verification_audit'] == _review_cache._read_all(readonly=True)['ABCDEFGH']['verification_audit']
    assert original.read_bytes() == b'Synthetic attached source'
    assert _review_cache._cache_path().read_bytes() == cache_before


def test_identity_export_and_same_stat_drift_without_python311_file_digest(audit_env, monkeypatch):
    monkeypatch.delattr(hashlib, 'file_digest', raising=False)
    entry, _, path = publish(audit_env)
    payload = get(audit_env).json()
    assert payload['audit_status'] == 'available'
    assert payload['verification_audit'] == entry['verification_audit']
    before = _review_cache._cache_path().read_bytes()
    stat = path.stat()
    path.write_bytes(b'x' * stat.st_size)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert get(audit_env).json()['reason'] == 'source_bytes_changed'
    assert _review_cache._cache_path().read_bytes() == before
