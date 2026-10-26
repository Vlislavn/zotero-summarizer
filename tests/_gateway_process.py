"""Owned, bounded real-Uvicorn fixture processes; no Tailscale or product lifespan."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
READY_STEPS = int(os.environ.get("ZS_GATEWAY_TEST_READY_STEPS", "200"))
SAFETY_SECONDS = float(os.environ.get("ZS_GATEWAY_TEST_SAFETY_SECONDS", "15"))
POLL_SECONDS = 0.05
assert 1 <= READY_STEPS <= 600 and 1 <= SAFETY_SECONDS <= 60
ORIGIN = "https://reader.example-tailnet.ts.net"
HEADERS = {
    "Host": "localhost", "Tailscale-User-Login": "owner@example.net",
    "X-Forwarded-Host": "reader.example-tailnet.ts.net", "X-Forwarded-Proto": "https",
    "Origin": ORIGIN, "Sec-Fetch-Site": "same-origin",
}
CHILD = r'''
import sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
import json, types, os
from argparse import Namespace
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from zotero_summarizer.api.local_access import LocalAccessMiddleware
from zotero_summarizer.api.mobile_access import MobileAccessMiddleware
from zotero_summarizer.cli import _mobile_serve as mobile
from zotero_summarizer import settings as settings_module
for module in (mobile, settings_module):
    assert Path(module.__file__).resolve().is_relative_to(root)
async def probe(request: Request):
    return JSONResponse({'base_url': str(request.base_url), 'host': request.headers['host'],
        'origin': request.headers.get('origin'), 'body': (await request.body()).decode()})
async def websocket(connection):
    await connection.accept()
    await connection.send_text('gateway fixture')
    await connection.close()
fixture = Starlette(routes=[Route('/api/probe', probe, methods=['GET','POST']),
    Route('/trailing/', probe), WebSocketRoute('/socket', websocket)])
mode, state, ready = sys.argv[2], sys.argv[3], Path(sys.argv[4])
original = uvicorn.Server
class ReadyServer(original):
    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        if self.started:
            address = self.servers[0].sockets[0].getsockname()
            source = {name: str(Path(module.__file__).resolve()) for name, module in sys.modules.items()
                if name.startswith('zotero_summarizer') and getattr(module, '__file__', None)}
            assert all(Path(path).is_relative_to(root) for path in source.values())
            pending = ready.with_suffix('.tmp')
            pending.write_text(json.dumps({'address': address, 'sourceRoots': source,
                'fixture': 'real Uvicorn + actual middleware, synthetic identity headers; no product lifespan/TLS'}))
            pending.replace(ready)
uvicorn.Server = ReadyServer
if mode == 'mobile':
    app = types.ModuleType('zotero_summarizer.api.app')
    app.create_app = lambda settings, *, mobile_access: MobileAccessMiddleware(fixture, mobile_access)
    sys.modules[app.__name__] = app
    raise SystemExit(mobile._serve_mobile(Namespace(project_root=state,
        origin='https://reader.example-tailnet.ts.net', owner_login='owner@example.net')))
shutdown_seconds = int(os.environ.get('ZS_GATEWAY_TEST_SHUTDOWN_SECONDS', '3'))
assert 1 <= shutdown_seconds <= 15
config = uvicorn.Config(LocalAccessMiddleware(fixture), host='127.0.0.1', port=0,
    proxy_headers=False, http='h11', access_log=False, timeout_graceful_shutdown=shutdown_seconds)
ReadyServer(config).run()
'''


@contextmanager
def owned_server(tmp_path, mode):
    ready = tmp_path / (mode + "-ready.json")
    with tempfile.TemporaryDirectory(prefix="zsgw-", dir="/tmp") as directory:
        with (tmp_path / (mode + "-server.log")).open("w") as log:
            child = subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", CHILD, str(ROOT), mode, directory, str(ready)],
                cwd=ROOT, env=dict(os.environ), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            )
            try:
                for _step in range(READY_STEPS):
                    if ready.exists():
                        break
                    assert child.poll() is None, (tmp_path / (mode + "-server.log")).read_text()
                    time.sleep(POLL_SECONDS)
                else:
                    raise AssertionError("owned server did not become ready within step budget")
                info = json.loads(ready.read_text())
                yield child, info
            finally:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=SAFETY_SECONDS)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=SAFETY_SECONDS)


def wire(path, request, *, stop_at=None):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(SAFETY_SECONDS)
        client.connect(path)
        client.sendall(request)
        parts = []
        for _step in range(READY_STEPS):
            part = client.recv(65536)
            if not part:
                break
            parts.append(part)
            if stop_at is not None and stop_at in b"".join(parts):
                break
        else:
            raise AssertionError("fixture response exceeded bounded receive work")
        return b"".join(parts)


def get(path, endpoint="/api/probe", headers=None):
    fields = HEADERS if headers is None else headers
    request = f"GET {endpoint} HTTP/1.1\r\n" + "".join(f"{key}: {value}\r\n" for key, value in fields.items())
    return wire(path, (request + "Connection: close\r\n\r\n").encode("ascii"))
