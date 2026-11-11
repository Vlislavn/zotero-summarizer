"""Read-only installed-model snapshot into an isolated native store; no pulls."""
import errno
import hashlib
from ipaddress import ip_address
import json
import os
from pathlib import Path
import re
import shlex

import httpx


def _local_url(value):
    url = httpx.URL(value)
    if (url.scheme != 'http' or url.username or url.password or url.query or url.fragment
            or not ip_address(url.host).is_loopback or url.path not in ('', '/')):
        raise ValueError('Native runtime requires an explicit numeric HTTP loopback origin')
    return str(url).rstrip('/')


def _batch_manifest(manifest, destination, batch_size):
    if type(batch_size) is not int or not 1 <= batch_size <= 1024:
        raise ValueError('Native batch size must be an integer in [1, 1024]')
    kind = 'application/vnd.ollama.image.params'
    layers = [row for row in manifest['layers'] if row['mediaType'] == kind]
    if len(layers) > 1:
        raise ValueError('Installed model has ambiguous native parameter layers')
    parameters = {}
    if layers:
        filename = layers[0]['digest'].replace(':', '-')
        parameters = json.loads((destination / 'blobs' / filename).read_bytes())
    parameters['num_batch'] = batch_size
    content = json.dumps(parameters, sort_keys=True, separators=(',', ':')).encode()
    digest = 'sha256:' + hashlib.sha256(content).hexdigest()
    path = destination / 'blobs' / digest.replace(':', '-')
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError('Existing native parameter blob differs from its identity')
    else:
        with path.open('xb') as output:
            output.write(content)
    layer = {'mediaType': kind, 'digest': digest, 'size': len(content)}
    manifest['layers'] = [row for row in manifest['layers'] if row['mediaType'] != kind] + [layer]
    return json.dumps(manifest, separators=(',', ':')).encode()


def _clone_installed(source_url, model, destination, *, batch_size=None):
    source_url = _local_url(source_url)
    if re.fullmatch(r'[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+', model) is None:
        raise ValueError('Use an installed canonical model:tag name')
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=5) as client:
        response = client.get(source_url + '/api/tags')
        response.raise_for_status()
        matches = [row for row in response.json()['models'] if row['name'] == model]
        if len(matches) != 1:
            raise ValueError('Exact installed model identity was not found')
        selected = matches[0]
        response = client.post(source_url + '/api/show', json={'model': model})
        response.raise_for_status()
        modelfile = response.json()['modelfile']
    sources = [shlex.split(line)[1] for line in modelfile.splitlines() if line.startswith('FROM ')]
    if len(sources) != 1:
        raise ValueError('Installed model has no unique local weight source')
    # Metadata belongs to the installed store, not a linked weight's volume.
    primary = Path(sources[0]).absolute()
    if primary.parent.name != 'blobs':
        raise ValueError('Installed weights are not in a native content-addressed store')
    store = primary.parent.parent
    name, tag = model.rsplit(':', 1)
    relative = Path('manifests/registry.ollama.ai/library') / name / tag
    content = (store / relative).read_bytes()
    if hashlib.sha256(content).hexdigest() != selected['digest']:
        raise ValueError('Installed manifest differs from native inventory digest')
    manifest = json.loads(content)
    layers = [manifest['config'], *manifest['layers']]
    total = sum(row['size'] for row in layers)
    if total >= 4 * 1024**3:
        raise ValueError('Installed assets leave no defensible room within the owned 4GiB cap')
    destination = Path(destination)
    (destination / 'blobs').mkdir(parents=True, exist_ok=False)
    for layer in layers:
        if re.fullmatch(r'sha256:[0-9a-f]{64}', layer['digest']) is None:
            raise ValueError('Invalid installed blob identity')
        filename = layer['digest'].replace(':', '-')
        source = (store / 'blobs' / filename).resolve(strict=True)
        if source.stat().st_size != layer['size']:
            raise ValueError('Installed blob size changed')
        target = destination / 'blobs' / filename
        try:
            os.link(source, target)
        except OSError as error:
            if error.errno != errno.EXDEV:
                raise
            target.symlink_to(source)
    if batch_size is not None:
        content = _batch_manifest(manifest, destination, batch_size)
    target = destination / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(content)
    return {'name': model, 'digest': hashlib.sha256(content).hexdigest(),
            'source_digest': selected['digest'], 'batch_size': batch_size, 'declared_bytes': total,
            'manifest_sha256_verified': True,
            'model_path': str(destination / 'blobs' / primary.name)}
