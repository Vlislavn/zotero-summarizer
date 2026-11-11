"""Explicit trusted-localhost gateway contracts; fixtures are not real Tailscale identities."""
from contextlib import contextmanager
import socket

import pytest

from _gateway_process import HEADERS, ORIGIN, get, owned_server, wire
from test_mobile_access_boundary import _client, _headers, LOGIN
from test_gateway_app_surface import LOOPBACK_PORT as PORT, surface
from zotero_summarizer.api.mobile_access import MobileAccessPolicy
from zotero_summarizer.cli import build_parser

_NATIVE_CONNECT = socket.socket.connect


def _scope(port=PORT, *, peer=("127.0.0.1", 45000), server=None, headers=None):
    fields = {**HEADERS, "Host": ORIGIN.removeprefix("https://")} if headers is None else headers
    return {"type": "http", "scheme": "http", "client": peer,
            "server": ("127.0.0.1", port) if server is None else server,
            "headers": [(key.lower().encode(), value.encode()) for key, value in fields.items()]}


def test_localhost_transport_is_explicit_and_retains_real_serve_authority():
    policy = MobileAccessPolicy(ORIGIN, LOGIN, trusted_loopback_port=PORT)
    assert policy.allows(_scope())
    assert policy.socket_path is None
    for key in [b"host", b"tailscale-user-login", b"x-forwarded-host", b"x-forwarded-proto", b"origin", b"sec-fetch-site"]:
        duplicated = _scope()
        duplicated["headers"].append(next(field for field in duplicated["headers"] if field[0] == key))
        assert not policy.allows(duplicated)
    missing = _scope()
    missing["headers"] = [(key, value) for key, value in missing["headers"] if key != b"tailscale-user-login"]
    assert not policy.allows(missing)
    for host in [ORIGIN.removeprefix("https://").upper(), ORIGIN.removeprefix("https://") + ":443"]:
        assert policy.allows(_scope(headers={**HEADERS, "Host": host}))


@pytest.mark.parametrize("peer,server", [
    (None, ("127.0.0.1", PORT)), (("192.0.2.1", 45000), ("127.0.0.1", PORT)),
    (("::1", 45000), ("127.0.0.1", PORT)), (("127.0.0.1", 45000), ("0.0.0.0", PORT)),
    (("127.0.0.1", 45000), ("127.0.0.1", PORT + 1)), (("127.0.0.1", 45000), ("/tmp/api.sock", None)),
])
def test_localhost_policy_refuses_other_peer_bind_and_transport(peer, server):
    policy = MobileAccessPolicy(ORIGIN, LOGIN, trusted_loopback_port=PORT)
    assert not policy.allows(_scope(peer=peer, server=server))


@pytest.mark.parametrize("key,value", [
    ("Tailscale-User-Login", "other@example.net"), ("Tailscale-User-Login", ""),
    ("Host", "localhost"), ("Host", "other.ts.net"), ("X-Forwarded-Host", "other.ts.net"),
    ("X-Forwarded-Proto", "http"), ("Origin", "https://other.ts.net"),
    ("Sec-Fetch-Site", "cross-site"), ("Tailscale-Funnel-Request", "?1"),
])
def test_localhost_policy_keeps_owner_origin_and_funnel_refusals(key, value):
    policy = MobileAccessPolicy(ORIGIN, LOGIN, trusted_loopback_port=PORT)
    headers = {**HEADERS, "Host": ORIGIN.removeprefix("https://"), key: value}
    assert not policy.allows(_scope(headers=headers))


@pytest.mark.parametrize("port", [True, 0, -1, 443, 65536, "8000", 8000.5])
def test_invalid_localhost_port_is_not_a_policy(port):
    with pytest.raises(ValueError):
        MobileAccessPolicy(ORIGIN, LOGIN, trusted_loopback_port=port)


def test_policy_selects_exactly_one_transport(tmp_path):
    with pytest.raises(ValueError):
        MobileAccessPolicy(ORIGIN, LOGIN)
    with pytest.raises(ValueError):
        MobileAccessPolicy(ORIGIN, LOGIN, tmp_path / "api.sock", trusted_loopback_port=PORT)


@pytest.mark.parametrize("surface", ["trusted-loopback"], indirect=True)
@pytest.mark.parametrize("endpoint", ["/", "/assets/fixture.js", "/sw.js", "/docs", "/api/sync/status"])
def test_localhost_factory_denies_non_owner_before_any_surface(surface, endpoint):
    headers = dict(_headers())
    headers.update(Host=ORIGIN.removeprefix("https://"), **{"Tailscale-User-Login": "other@example.net"})
    with _client(surface, peer=("127.0.0.1", 45000), server=("127.0.0.1", PORT)) as client:
        response = client.get(endpoint, headers=headers)
    assert response.status_code == 403
    assert response.json()["error"] == "mobile_access_denied"


def test_parser_requires_explicit_localhost_selection():
    arguments = ["serve-mobile", "--origin", ORIGIN, "--owner-login", LOGIN]
    assert build_parser().parse_args(arguments).trusted_loopback_port is None
    assert build_parser().parse_args([*arguments, "--trusted-loopback-port", str(PORT)]).trusted_loopback_port == PORT


@contextmanager
def _owned_tcp(tmp_path, monkeypatch):
    with owned_server(tmp_path, "mobile-loopback") as value:
        _child, info = value
        address = tuple(info["address"])
        assert address[0] == "127.0.0.1"

        def connect(sock, target):
            if sock.family in (socket.AF_INET, socket.AF_INET6) and target != address:
                raise AssertionError("Only this fixture's exact localhost address is admitted")
            return _NATIVE_CONNECT(sock, target)

        monkeypatch.setattr(socket.socket, "connect", connect)
        yield value


def test_real_localhost_launcher_serves_owner_redirect_and_denies_non_owner(tmp_path, monkeypatch):
    headers = {**HEADERS, "Host": ORIGIN.removeprefix("https://")}
    with _owned_tcp(tmp_path, monkeypatch) as (_child, info):
        address = info["address"]
        accepted = get(address, headers=headers)
        assert accepted.startswith(b"HTTP/1.1 200")
        assert (ORIGIN + "/").encode() in accepted
        redirected = get(address, "/trailing", headers=headers)
        assert ("location: " + ORIGIN + "/trailing/").encode() in redirected
        anonymous = {key: value for key, value in headers.items() if key != "Tailscale-User-Login"}
        for denied_headers in [anonymous, {**headers, "Tailscale-User-Login": "other@example.net"},
                               {**headers, "Tailscale-Funnel-Request": "?1"}]:
            assert get(address, headers=denied_headers).startswith(b"HTTP/1.1 403")
        from _gateway_process import SAFETY_SECONDS
        _child.terminate()
        _child.wait(timeout=SAFETY_SECONDS)
        assert _child.returncode != 0
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as replacement:
            replacement.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            replacement.bind(tuple(address))


def test_real_localhost_websocket_keeps_owner_gate(tmp_path, monkeypatch):
    import base64
    from secrets import token_bytes

    with _owned_tcp(tmp_path, monkeypatch) as (_child, info):
        headers = {**HEADERS, "Host": ORIGIN.removeprefix("https://"), "Connection": "Upgrade",
                   "Upgrade": "websocket", "Sec-WebSocket-Version": "13",
                   "Sec-WebSocket-Key": base64.b64encode(token_bytes(16)).decode("ascii")}
        request = "GET /socket HTTP/1.1\r\n" + "".join(f"{key}: {value}\r\n" for key, value in headers.items()) + "\r\n"
        assert wire(info["address"], request.encode(), stop_at=b"gateway fixture").startswith(b"HTTP/1.1 101")
        headers["Tailscale-User-Login"] = "other@example.net"
        request = "GET /socket HTTP/1.1\r\n" + "".join(f"{key}: {value}\r\n" for key, value in headers.items()) + "\r\n"
        assert wire(info["address"], request.encode()).startswith(b"HTTP/1.1 403")
