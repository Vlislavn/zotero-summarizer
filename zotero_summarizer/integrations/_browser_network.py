"""Public-only browser egress. CONNECT preserves browser TLS and authentication.

One short-lived loopback proxy per browser session, with no DIRECT fallback.
Every connection resolves once through the RSS/PDF public-address boundary and
connects to that numeric address. Redirects, API requests, workers and subframes
cannot delegate a second DNS lookup to Chromium. This is not a browser sandbox
or a decoded-response memory limit (see audit A111).
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import secrets
import select
import socket
import sys
import threading
from time import monotonic

from zotero_summarizer.integrations.app_rss import RssUrlRejected, _resolve_public_url


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
        target, address = _resolve_public_url("https://" + self.path if tunnel else self.path)
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
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(("127.0.0.1", 0), _Request)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            if not self.errors:
                self.errors.append(RuntimeError("Browser proxy connection limit exceeded"))
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
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
