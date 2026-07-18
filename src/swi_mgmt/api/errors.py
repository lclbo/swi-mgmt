"""SNMP error formatting for API responses."""

from __future__ import annotations

from swi_mgmt.config import SwitchConfig
from swi_mgmt.snmp.client import SnmpError


def format_snmp_error(host: str, cfg: SwitchConfig | None, exc: Exception) -> str:
    """Turn low-level SNMP errors into actionable messages."""
    msg = str(exc).strip()
    lower = msg.lower()

    if isinstance(exc, SnmpError) and "timeout" in lower:
        parts = [f"SNMP timeout contacting {host}"]
        if cfg:
            parts.append(f"({cfg.snmp_auth_summary()})")
        if cfg and cfg.snmp_version == 3:
            parts.append(
                "Check that the switch is online, SNMPv3 is enabled, the USM user/"
                "auth/priv settings match, and UDP port 161 is reachable from this machine."
            )
        else:
            parts.append(
                "Check that the switch is online, SNMP is enabled, the community string "
                "and version match, and UDP port 161 is reachable from this machine."
            )
        return " ".join(parts)

    if isinstance(exc, SnmpError):
        if cfg:
            return f"SNMP error for {host} ({cfg.snmp_auth_summary()}): {msg}"
        return f"SNMP error for {host}: {msg}"

    if cfg:
        return f"Failed to query {host} ({cfg.snmp_auth_summary()}): {msg}"
    return f"Failed to query {host}: {msg}"
