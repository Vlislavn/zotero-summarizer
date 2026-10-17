"""The supported deployment is local-only; remote authentication is not configured."""
from __future__ import annotations

import ipaddress


LOCAL_ONLY_MESSAGE = "Only loopback access is supported; authenticated HTTPS mobile deployment is not configured."


def is_loopback_address(host: str) -> bool:
    if "%" in host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


def is_loopback_host(host: str) -> bool:
    return host.lower() == "localhost" or is_loopback_address(host)


def require_loopback_bind(host: str) -> str:
    if not is_loopback_host(host):
        raise ValueError(LOCAL_ONLY_MESSAGE)
    return host
