"""Actual saved Vite proxy -> local admission -> fixture POST, not a browser/device."""
import http.client
import json
import os
import socket
import subprocess
import time

import pytest

from _gateway_process import POLL_SECONDS, READY_STEPS, ROOT, SAFETY_SECONDS, owned_server


# Captured before the network-denying autouse fixture; re-enabled only for owned ports.
_NATIVE_CONNECT = socket.socket.connect


def test_actual_vite_proxy_preserves_browser_authority_and_foreign_origin_refusal(tmp_path, monkeypatch):
    import shutil

    executable = shutil.which("node")
    frontend = ROOT / "frontend"
    vite = frontend / "node_modules" / "vite" / "bin" / "vite.js"
    if executable is None or not vite.is_file():
        pytest.skip("Real Vite integration requires existing Node/frontend dependencies; no automatic install")
    with owned_server(tmp_path, "local") as (_backend, info):
        address = tuple(info["address"])
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        allowed = {address, ("127.0.0.1", port)}

        def connect(sock, target):
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                assert tuple(target) in allowed, target
            return _NATIVE_CONNECT(sock, target)

        monkeypatch.setattr(socket.socket, "connect", connect)
        config = tmp_path / "vite.config.mjs"
        config.write_text(
            "import config from " + json.dumps((frontend / "vite.config.js").as_uri()) + ";\n"
            "export default {...config, cacheDir: " + json.dumps(str(tmp_path / "vite-cache")) + ",\n"
            "  server: {...config.server, host: '127.0.0.1', port: " + str(port) + ", strictPort: true},\n"
            "  optimizeDeps: {noDiscovery: true, include: []}};\n"
        )
        env = {**os.environ, "VITE_API_TARGET": f"http://{address[0]}:{address[1]}"}
        origin = f"http://127.0.0.1:{port}"
        with (tmp_path / "vite.log").open("w") as log:
            child = subprocess.Popen(
                [executable, str(vite), "--config", str(config)], cwd=frontend, env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            )
            try:
                for _step in range(READY_STEPS):
                    assert child.poll() is None, (tmp_path / "vite.log").read_text()
                    try:
                        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                            client.settimeout(SAFETY_SECONDS)
                            client.connect(("127.0.0.1", port))
                        break
                    except ConnectionRefusedError:
                        time.sleep(POLL_SECONDS)
                else:
                    raise AssertionError("owned Vite did not listen within step budget")
                client = http.client.HTTPConnection("127.0.0.1", port, timeout=SAFETY_SECONDS)
                try:
                    client.request("POST", "/api/probe", b"fixture mutation", {
                        "Origin": origin, "Sec-Fetch-Site": "same-origin",
                    })
                    response = client.getresponse()
                    body = response.read()
                    assert response.status == 200, body
                    assert json.loads(body) == {
                        "base_url": origin + "/", "host": f"127.0.0.1:{port}",
                        "origin": origin, "body": "fixture mutation",
                    }
                    client.request("POST", "/api/probe", b"must not reach handler", {"Origin": "http://evil.invalid"})
                    rejected = client.getresponse()
                    assert rejected.status == 403
                    assert json.loads(rejected.read())["error"] == "local_access_only"
                finally:
                    client.close()
            finally:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=SAFETY_SECONDS)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=SAFETY_SECONDS)
