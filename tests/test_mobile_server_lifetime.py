"""Owned subprocess probes of the real launcher/Uvicorn signal lifetime, not TLS."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time

import pytest

from zotero_summarizer.cli._mobile_serve import private_listener
from zotero_summarizer.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
READY_STEPS = int(os.environ.get('ZS_MOBILE_SIGNAL_READY_STEPS', '100'))
SAFETY_SECONDS = float(os.environ.get('ZS_MOBILE_SIGNAL_SAFETY_SECONDS', '10'))
assert 1 <= READY_STEPS <= 500 and 1 <= SAFETY_SECONDS <= 30
POLL_SECONDS = 0.05
CHILD = r'''
import sys
import types
import importlib.util
from argparse import Namespace
from pathlib import Path
async def trivial(scope, receive, send):
    if scope['type'] == 'lifespan':
        while True:
            message = await receive()
            if message['type'] == 'lifespan.startup':
                await send({'type': 'lifespan.startup.complete'})
            elif message['type'] == 'lifespan.shutdown':
                await send({'type': 'lifespan.shutdown.complete'})
                return
    elif scope['type'] == 'http':
        await send({'type': 'http.response.start', 'status': 204, 'headers': []})
        await send({'type': 'http.response.body', 'body': b''})
api = types.ModuleType('zotero_summarizer.api.app')
api.create_app = lambda *args, **kwargs: trivial
sys.modules[api.__name__] = api
source = Path(sys.argv[2]) / 'zotero_summarizer/cli/_mobile_serve.py'
spec = importlib.util.spec_from_file_location('_owned_mobile_launcher', source)
mobile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mobile)
assert Path(mobile.__file__).resolve().is_relative_to(Path(sys.argv[2]).resolve())
if len(sys.argv) > 3:
    import os, signal, socket, uvicorn
    stage, signum = sys.argv[3], int(sys.argv[4])
    if stage == 'bound':
        original_bind = socket.socket.bind
        def bind_then_interrupt(listener, address):
            original_bind(listener, address)
            os.kill(os.getpid(), signum)
        socket.socket.bind = bind_then_interrupt
    else:
        original_run = uvicorn.Server.run
        def interrupt_then_run(server, *args, **kwargs):
            os.kill(os.getpid(), signum)
            return original_run(server, *args, **kwargs)
        uvicorn.Server.run = interrupt_then_run
raise SystemExit(mobile._serve_mobile(Namespace(project_root=sys.argv[1],
    origin='https://reader.example-tailnet.ts.net', owner_login='owner@example.net')))
'''


def _ready(path):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(SAFETY_SECONDS)
            client.connect(str(path))
            client.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
            return client.recv(512).startswith(b'HTTP/1.1 204')
    except (FileNotFoundError, ConnectionRefusedError):
        return False


@pytest.mark.parametrize('signum', [signal.SIGTERM, signal.SIGINT], ids=['SIGTERM', 'SIGINT'])
def test_real_mobile_launcher_signal_cleans_owned_socket_and_allows_restart(tmp_path, signum):
    with tempfile.TemporaryDirectory(prefix='zsms-', dir='/tmp') as directory:
        settings = Settings.load(project_root=Path(directory))
        path = settings.data_dir.absolute() / 'mobile' / 'api.sock'
        with (tmp_path / 'child.log').open('w') as log:
            child = subprocess.Popen([sys.executable, '-B', '-c', CHILD, directory, str(ROOT)],
                cwd=ROOT, env={**os.environ, 'PYTHONPATH': str(ROOT), 'PYTHONDONTWRITEBYTECODE': '1'},
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            try:
                for step in range(READY_STEPS):
                    if _ready(path):
                        break
                    assert child.poll() is None, (tmp_path / 'child.log').read_text()
                    time.sleep(POLL_SECONDS)
                else:
                    raise AssertionError('owned server did not become ready within work budget')
                child.send_signal(signum)
                child.wait(timeout=SAFETY_SECONDS)
                (tmp_path / 'lifetime.json').write_text(json.dumps({
                    'signal': signum, 'returncode': child.returncode,
                    'socket_exists_after_exit': path.exists(), 'source_root': str(ROOT), 'ready_step': step,
                }, indent=2))
                assert not path.exists(), 'graceful signal left the owned socket behind'
                with private_listener(settings):
                    assert path.is_socket()
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=SAFETY_SECONDS)


@pytest.mark.parametrize('stage', ['bound', 'before-serve'])
@pytest.mark.parametrize('signum', [signal.SIGTERM, signal.SIGINT], ids=['SIGTERM', 'SIGINT'])
def test_startup_interruptions_release_socket_before_server_signal_capture(tmp_path, stage, signum):
    with tempfile.TemporaryDirectory(prefix='zsearly-', dir='/tmp') as directory:
        settings = Settings.load(project_root=Path(directory))
        path = settings.data_dir / 'mobile' / 'api.sock'
        result = subprocess.run(
            [sys.executable, '-B', '-c', CHILD, directory, str(ROOT), stage, str(int(signum))],
            cwd=ROOT, env={**os.environ, 'PYTHONPATH': str(ROOT), 'PYTHONDONTWRITEBYTECODE': '1'},
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=SAFETY_SECONDS,
        )
        (tmp_path / 'early-interruption.log').write_text(result.stdout + result.stderr)
        assert result.returncode != 0
        assert not path.exists(), f'{stage}: interruption left the owned socket behind'
        with private_listener(settings):
            assert path.is_socket()
