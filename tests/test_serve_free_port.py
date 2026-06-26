"""`serve` reclaims its port instead of dying on 'address already in use'."""
from __future__ import annotations

import socket
import subprocess
import sys
import time

from zotero_summarizer.cli._app import _free_port


def _free_tcp_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_free_port_kills_the_squatter_and_releases_the_socket():
    port = _free_tcp_port()
    # A child process that binds + listens on the port and then sleeps — exactly
    # the leftover-server situation that triggers uvicorn's Errno 48.
    code = (
        "import socket,time;"
        f"s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
        f"s.bind(('127.0.0.1',{port}));s.listen();"
        "import sys;print('up',flush=True);time.sleep(60)"
    )
    child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE)
    assert child.stdout.readline().strip() == b"up"  # listener is bound

    _free_port(port)

    assert child.wait(timeout=5) is not None  # squatter was stopped
    # The socket is now rebindable (the whole point).
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.close()


def test_free_port_noop_when_nothing_listening():
    _free_port(_free_tcp_port())  # must not raise when the port is free
