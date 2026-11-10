"""Installed immutable blob reuse and refusal before native startup; no weights load."""
import errno
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from zotero_summarizer.integrations import _ollama_store
from zotero_summarizer.integrations.ollama_runtime import OwnedOllamaRuntime


@pytest.fixture
def installed_store(tmp_path, monkeypatch):
    source = tmp_path / 'original'
    (source / 'blobs').mkdir(parents=True)
    weight = b'inert fixture weights'
    digest = hashlib.sha256(weight).hexdigest()
    blob = source / 'blobs' / ('sha256-' + digest)
    blob.write_bytes(weight)
    manifest = {'config': {'digest': 'sha256:' + digest, 'size': len(weight)}, 'layers': []}
    content = json.dumps(manifest).encode()
    relative = Path('manifests/registry.ollama.ai/library/fixture/latest')
    path = source / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    requests = []

    def respond(request):
        requests.append(request.url.path)
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': 'fixture:latest',
                'digest': hashlib.sha256(content).hexdigest()}]})
        assert request.url.path == '/api/show'
        return httpx.Response(200, json={'modelfile': f'FROM {blob}'})

    client_type = httpx.Client
    monkeypatch.setattr(_ollama_store.httpx, 'Client', lambda **_kwargs: client_type(
        transport=httpx.MockTransport(respond), trust_env=False))
    return blob, relative, content, requests


@pytest.mark.parametrize('linked_source', [False, True])
@pytest.mark.parametrize('cross_volume', [False, True])
def test_isolated_store_reuses_exact_installed_bytes_without_pull_or_source_change(
        tmp_path, monkeypatch, installed_store, linked_source, cross_volume):
    blob, relative, content, requests = installed_store
    weight = blob.read_bytes()
    if linked_source:
        external = tmp_path / 'external-volume' / 'blobs' / blob.name
        external.parent.mkdir(parents=True)
        blob.rename(external)
        blob.symlink_to(external)
    before = blob.stat()
    if cross_volume:
        def different_filesystems(*_args):
            raise OSError(errno.EXDEV, 'Cross-device link')
        monkeypatch.setattr(_ollama_store.os, 'link', different_filesystems)
    destination = tmp_path / 'isolated'
    result = _ollama_store._clone_installed('http://127.0.0.1:12345', 'fixture:latest', destination)
    prepared_blob = destination / 'blobs' / blob.name
    assert result['manifest_sha256_verified'] is True
    assert result['model_path'] == str(prepared_blob)
    assert (destination / relative).read_bytes() == content
    assert prepared_blob.is_symlink() is cross_volume
    assert prepared_blob.read_bytes() == weight
    assert prepared_blob.stat().st_ino == before.st_ino
    if cross_volume:
        assert prepared_blob.readlink() == blob.resolve(strict=True)
    assert blob.read_bytes() == weight and blob.stat().st_mtime_ns == before.st_mtime_ns
    prepared_blob.unlink()
    assert blob.read_bytes() == weight
    assert requests == ['/api/tags', '/api/show']


@pytest.mark.parametrize('error_number', [errno.EACCES, errno.EPERM, errno.ENOSPC])
def test_store_does_not_hide_other_link_failures(tmp_path, monkeypatch, installed_store, error_number):
    blob, _, _, _ = installed_store
    before = blob.read_bytes()

    def refused(*_args):
        raise OSError(error_number, 'Link refused')

    monkeypatch.setattr(_ollama_store.os, 'link', refused)
    destination = tmp_path / 'refused'
    with pytest.raises(OSError) as raised:
        _ollama_store._clone_installed('http://127.0.0.1:12345', 'fixture:latest', destination)
    assert raised.value.errno == error_number
    assert not (destination / 'blobs' / blob.name).is_symlink()
    assert blob.read_bytes() == before


@pytest.mark.parametrize('missing', [False, True])
def test_unavailable_or_changed_external_weights_refuse_before_preparation(
        tmp_path, installed_store, missing):
    blob, _, _, requests = installed_store
    external = tmp_path / 'external-weight'
    blob.rename(external)
    blob.symlink_to(external)
    if missing:
        external.unlink()
    else:
        external.write_bytes(b'changed')
    with pytest.raises(FileNotFoundError if missing else ValueError,
                       match=None if missing else 'Installed blob size changed'):
        _ollama_store._clone_installed('http://127.0.0.1:12345', 'fixture:latest', tmp_path / 'refused')
    assert requests == ['/api/tags', '/api/show']


@pytest.mark.parametrize('url', ['https://example.invalid', 'http://0.0.0.0:1',
                               'http://user:secret@127.0.0.1:1', 'http://127.0.0.1:1/foreign'])
def test_native_metadata_origin_is_strict_loopback(url):
    with pytest.raises(ValueError):
        _ollama_store._local_url(url)


@pytest.mark.parametrize('batch_size', [32, 64, 128])
@pytest.mark.parametrize('symbolic', [False, True])
def test_batch_profile_changes_only_isolated_parameter_manifest_not_linked_source(tmp_path, batch_size, symbolic):
    import os

    original = tmp_path / 'original-parameters'
    original.write_text(json.dumps({'temperature': 0.7, 'num_batch': 1024}))
    before = original.read_bytes()
    digest = hashlib.sha256(before).hexdigest()
    destination = tmp_path / 'isolated'
    (destination / 'blobs').mkdir(parents=True)
    prepared_source = destination / 'blobs' / ('sha256-' + digest)
    if symbolic:
        prepared_source.symlink_to(original)
    else:
        os.link(original, prepared_source)
    kind = 'application/vnd.ollama.image.params'
    manifest = {'layers': [{'mediaType': kind, 'digest': 'sha256:' + digest, 'size': len(before)}]}
    encoded = _ollama_store._batch_manifest(manifest, destination, batch_size)
    prepared = json.loads(encoded)['layers'][0]
    value = json.loads((destination / 'blobs' / prepared['digest'].replace(':', '-')).read_bytes())
    assert value == {'temperature': 0.7, 'num_batch': batch_size}
    assert original.read_bytes() == before
    assert prepared['digest'] != 'sha256:' + digest


def test_missing_guard_refuses_before_store_or_daemon_creation(tmp_path):
    directory = tmp_path / 'not-created'
    runtime = OwnedOllamaRuntime('http://127.0.0.1:11434', 'fixture:latest', directory, tmp_path / 'missing.json')
    with pytest.raises(FileNotFoundError):
        with runtime:
            pytest.fail('Missing guardian admitted preparation')
    assert not directory.exists() and runtime.process is None


def test_service_only_floor_cannot_authorize_model_preparation(tmp_path):
    receipt = tmp_path / 'wrong-guard.json'
    receipt.write_text(json.dumps({'owner': 'local-evaluation-v1', 'mode': 'finite', 'state': 'running',
        'limits': {'min_free_percent': 12, 'rss_mib': 4096, 'swap_growth_mib': 2048, 'wall_seconds': 100}}))
    directory = tmp_path / 'not-created'
    runtime = OwnedOllamaRuntime('http://127.0.0.1:11434', 'fixture:latest', directory, receipt)
    with pytest.raises(RuntimeError, match='guardian'):
        with runtime:
            pytest.fail('Service exception admitted model preparation')
    assert not directory.exists()
