"""JSON serialization for API responses."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import IntEnum
from typing import Any

from swi_mgmt.config import AppConfig, SwitchConfig
from swi_mgmt.models.switch import SwitchSnapshot
from swi_mgmt.session.vlan_registry import VlanConflict
from swi_mgmt.snmp.scanner import ScanResult


def _convert(obj: Any) -> Any:
    if isinstance(obj, IntEnum):
        return obj.name
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _convert(v) for k, v in asdict(obj).items()}
    if isinstance(obj, list):
        return [_convert(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _convert(v) for k, v in obj.items()}
    return obj


def switch_config_dict(cfg: SwitchConfig, *, dns_name: str | None = None) -> dict:
    data = _convert(cfg)
    if dns_name:
        data["dns_name"] = dns_name
    return data


def app_config_dict(
    cfg: AppConfig, *, dns_by_host: dict[str, str | None] | None = None
) -> dict:
    data = _convert(cfg)
    if dns_by_host:
        for sw in data.get("switches", []):
            name = dns_by_host.get(sw.get("host", ""))
            if name:
                sw["dns_name"] = name
    return data


def snapshot_dict(snap: SwitchSnapshot) -> dict:
    return _convert(snap)


def scan_result_dict(r: ScanResult) -> dict:
    return _convert(r)


def conflict_dict(c: VlanConflict) -> dict:
    return _convert(c)
