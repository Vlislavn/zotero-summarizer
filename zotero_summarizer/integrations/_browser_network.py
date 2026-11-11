"""Public-only browser egress. CONNECT preserves browser TLS and authentication.

One short-lived loopback proxy per browser session, with no DIRECT fallback.
Every connection resolves once through the RSS/PDF public-address boundary and
connects to that numeric address. Redirects, API requests, workers and subframes
cannot delegate a second DNS lookup to Chromium. This is not a browser sandbox
or a decoded-response memory limit (see audit A111).
"""
from __future__ import annotations

import base64
import logging
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import secrets
import select
import socket
import sys
import threading
from time import monotonic

from zotero_summarizer.integrations.app_rss import RssHostUnresolved, RssUrlRejected, _resolve_public_url

LOGGER = logging.getLogger(__name__)
_MAX_CONNECTIONS = 32
# A heavy page bursts past the cap; surplus connections queue for a free slot this long.
_SLOT_WAIT_SECONDS = 15.0


def _relay(client: socket.socket, upstream: socket.socket, server: _Proxy) -> None:
    deadline = monotonic() + server.connection_timeout
    while not server.stopping.is_set():
        if monotonic() >= deadline:
            raise TimeoutError("Browser proxy connection exceeded its time budget")
        readable, _, _ = select.select([client, upstream], [], [], 0.1)
        for source in readable:
            chunk = source.recv(64 * 1024)
            if not chunk:
                return
            (upstream if source is client else client).sendall(chunk)


class _Request(BaseHTTPRequestHandler):
    # Avoid read-ahead: after parsing headers the raw sockets carry body/TLS bytes.
    rbufsize = 0
    timeout = 5

    def log_message(self, format, *args):
        # Request URLs and proxy credentials must not enter the application log.
        return

    def handle(self):
        self.raw_requestline = self.rfile.readline(65537)
        if len(self.raw_requestline) > 65536:
            raise RssUrlRejected("Browser proxy request line is too long")
        if not self.raw_requestline or not self.parse_request():
            return
        self.close_connection = True
        if not secrets.compare_digest(self.headers.get("Proxy-Authorization", ""), self.server.authorization):
            self.send_response(407)
            self.send_header("Proxy-Authenticate", 'Basic realm="paper-fetch"')
            self.send_header("Connection", "close")
            self.end_headers()
            return
        tunnel = self.command == "CONNECT"
        try:
            target, address = _resolve_public_url("https://" + self.path if tunnel else self.path)
        except RssHostUnresolved:
            # Chromium also requests background services unrelated to the paper.
            self.send_error(502, "Upstream hostname could not be resolved")
            return
        if tunnel and (target.raw_path != b"/" or target.fragment):
            raise RssUrlRejected("CONNECT requires a host and port, not a URL path")
        if not tunnel and target.scheme != "http":
            raise RssUrlRejected("HTTPS requires a CONNECT tunnel")
        port = target.port or (443 if tunnel else 80)
        with socket.create_connection((address, port), timeout=self.server.connection_timeout) as upstream:
            if tunnel:
                self.send_response(200, "Connection established")
                self.end_headers()
            else:
                headers = [(k, v) for k, v in self.headers.items()
                           if k.lower() not in {"proxy-authorization", "proxy-connection", "host", "connection"}]
                headers.extend([("Host", target.netloc.decode("ascii")), ("Connection", "close")])
                head = f"{self.command} {target.raw_path.decode('ascii')} HTTP/1.1\r\n"
                head += "".join(f"{k}: {v}\r\n" for k, v in headers) + "\r\n"
                upstream.sendall(head.encode("latin-1"))
            _relay(self.connection, upstream, self.server)


class _Proxy(ThreadingHTTPServer):
    def __init__(self, authorization: str, timeout: float):
        self.authorization = authorization
        self.connection_timeout = timeout
        self.stopping = threading.Event()
        self.errors: list[BaseException] = []
        # ponytail: bounded sockets for the single browser, not a general proxy service.
        self.slots = threading.BoundedSemaphore(_MAX_CONNECTIONS)
        super().__init__(("127.0.0.1", 0), _Request)

    def _wait_for_slot(self) -> bool:
        deadline = monotonic() + _SLOT_WAIT_SECONDS
        while not self.slots.acquire(timeout=0.1):
            if self.stopping.is_set() or monotonic() >= deadline:
                return False
        return True

    def process_request_thread(self, request, client_address):
        # Queue in the per-request thread (never the accept loop). A connection still without a
        # slot after the wait is dropped like any refused connection, which the browser retries;
        # it does not fail the whole session.
        if not self._wait_for_slot():
            LOGGER.warning("browser proxy: dropped a connection, all %d slots busy", _MAX_CONNECTIONS)
            self.shutdown_request(request)
            return
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        # socketserver dispatches on worker threads; rethrow on the calling thread
        # at session exit, before the caller can publish a successful PDF.
        if not self.errors:
            self.errors.append(sys.exception())


@contextmanager
def public_browser_options(url: str, timeout: float):
    if url:  # a blank headed login window has no initial destination
        _resolve_public_url(url)  # also rejects file/data URLs before browser launch
    password = secrets.token_urlsafe(32)
    authorization = "Basic " + base64.b64encode(f"paper:{password}".encode()).decode()
    with _Proxy(authorization, timeout) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1})
        thread.start()
        try:
            yield {
                "proxy": {"server": f"http://127.0.0.1:{server.server_port}",
                          "username": "paper", "password": password},
                "args": ["--proxy-bypass-list=<-loopback>", "--disable-quic",
                         "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"],
            }
        finally:
            server.stopping.set()
            server.shutdown()
            thread.join()
    if server.errors:
        raise server.errors[0]
