"""Hostname / reverse-DNS helpers."""

from __future__ import annotations

import ipaddress
import socket


def reverse_dns_lookup(host: str) -> str | None:
    """Return PTR hostname for an IP address, or None if unavailable.

    Non-IP hosts are skipped (already named). Lookup failures return None.
    """
    raw = (host or "").strip()
    if not raw:
        return None
    try:
        ipaddress.ip_address(raw)
    except ValueError:
        return None
    try:
        name, _aliases, _addrs = socket.gethostbyaddr(raw)
    except (socket.herror, socket.gaierror, OSError, TimeoutError):
        return None
    name = (name or "").strip().rstrip(".")
    if not name or name.lower() == raw.lower():
        return None
    return name
