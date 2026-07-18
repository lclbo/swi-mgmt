"""Persisted switch connection configuration."""

from __future__ import annotations

import ipaddress
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Optional

import yaml

SWITCH_ORDER_MODES = ("ip", "name", "type")


@dataclass
class SwitchConfig:
    host: str
    community: str = "public"
    snmp_version: int = 2  # 1 = v1, 2 = v2c, 3 = v3
    name: str = ""
    driver_id: str = ""  # empty = auto-detect
    port: int = 161
    # SNMPv3 USM (used only when snmp_version == 3)
    v3_user: str = ""
    v3_auth_proto: str = "sha"  # none|md5|sha|sha224|sha256|sha384|sha512
    v3_auth_key: str = ""
    v3_priv_proto: str = "aes128"  # none|des|aes128|aes192|aes256
    v3_priv_key: str = ""

    def display_name(self) -> str:
        return self.name or self.host

    def snmp_label(self) -> str:
        if self.snmp_version == 1:
            return "v1"
        if self.snmp_version == 3:
            return "v3"
        return "v2c"

    def snmp_auth_summary(self) -> str:
        if self.snmp_version == 3:
            user = (self.v3_user or "").strip() or "?"
            return f"user '{user}', SNMPv3, port {self.port}"
        return f"community '{self.community}', SNMPv{self.snmp_version}, port {self.port}"


@dataclass
class AppConfig:
    switches: list[SwitchConfig] = field(default_factory=list)
    # "ip" | "name" | "type" — list order used by UI and front panel
    switch_order: str = "ip"
    scan_community: str = "public"
    scan_version: int = 2
    scan_subnet: str = ""
    poll_interval_sec: float = 30.0
    snmp_timeout: float = 8.0
    snmp_retries: int = 2
    snmp_fast_mode: bool = True
    structure_cache_sec: float = 120.0
    prefetch_concurrency: int = 1


def normalize_switch_order(order: Optional[str]) -> str:
    """Normalize persisted/API order mode; legacy 'custom' becomes 'ip'."""
    value = str(order or "ip").lower().strip()
    if value == "custom":
        return "ip"
    if value in SWITCH_ORDER_MODES:
        return value
    return "ip"


def host_sort_key(host: str) -> tuple:
    """Sort key for switch hosts (IPv4 ascending, then other strings)."""
    try:
        addr = ipaddress.ip_address(host.strip())
        return (0, int(addr), "")
    except ValueError:
        return (1, 0, host.lower())


def switch_sort_key(sw: SwitchConfig, mode: str) -> tuple:
    ip_key = host_sort_key(sw.host)
    mode = normalize_switch_order(mode)
    if mode == "name":
        return (sw.display_name().lower(), ip_key)
    if mode == "type":
        # Empty driver_id means auto-detect; group those together.
        return ((sw.driver_id or "auto").lower(), ip_key)
    return (ip_key,)


def sort_switches(
    switches: list[SwitchConfig], mode: str = "ip"
) -> list[SwitchConfig]:
    return sorted(switches, key=lambda s: switch_sort_key(s, mode))


def sort_switches_by_ip(switches: list[SwitchConfig]) -> list[SwitchConfig]:
    return sort_switches(switches, "ip")


def apply_switch_order(config: AppConfig) -> bool:
    """Sort switches according to switch_order. Returns True if list changed."""
    mode = normalize_switch_order(config.switch_order)
    config.switch_order = mode
    ordered = sort_switches(config.switches, mode)
    if [s.host for s in ordered] == [s.host for s in config.switches]:
        return False
    config.switches = ordered
    return True


def ensure_switch_order(config: AppConfig) -> bool:
    """Apply configured sort mode (used on load)."""
    return apply_switch_order(config)


def insert_switch(config: AppConfig, cfg: SwitchConfig) -> None:
    """Append a switch and re-apply the current sort mode."""
    config.switches.append(cfg)
    apply_switch_order(config)


def config_path() -> Path:
    base = Path.home() / ".config" / "swi-mgmt"
    base.mkdir(parents=True, exist_ok=True)
    return base / "config.yaml"


def _switch_from_dict(raw: dict) -> SwitchConfig:
    """Build SwitchConfig from YAML/JSON, ignoring unknown keys."""
    allowed = {f.name for f in fields(SwitchConfig)}
    data = {k: v for k, v in raw.items() if k in allowed}
    sw = SwitchConfig(**data)
    try:
        sw.snmp_version = int(sw.snmp_version)
    except (TypeError, ValueError):
        sw.snmp_version = 2
    if sw.snmp_version not in (1, 2, 3):
        sw.snmp_version = 2
    return sw


def load_config() -> AppConfig:
    path = config_path()
    if not path.exists():
        return AppConfig()
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    switches = [_switch_from_dict(s) for s in data.get("switches", []) if isinstance(s, dict)]
    config = AppConfig(
        switches=switches,
        switch_order=normalize_switch_order(data.get("switch_order", "ip")),
        scan_community=data.get("scan_community", "public"),
        scan_version=data.get("scan_version", 2),
        scan_subnet=data.get("scan_subnet", ""),
        poll_interval_sec=data.get("poll_interval_sec", 30.0),
        snmp_timeout=data.get("snmp_timeout", 8.0),
        snmp_retries=data.get("snmp_retries", 2),
        snmp_fast_mode=bool(data.get("snmp_fast_mode", True)),
        structure_cache_sec=float(data.get("structure_cache_sec", 120.0)),
        prefetch_concurrency=int(data.get("prefetch_concurrency", 1)),
    )
    if ensure_switch_order(config):
        try:
            save_config(config)
        except OSError:
            pass
    return config


def save_config(config: AppConfig) -> None:
    path = config_path()
    data = {
        "switches": [asdict(s) for s in config.switches],
        "switch_order": normalize_switch_order(config.switch_order),
        "scan_community": config.scan_community,
        "scan_version": config.scan_version,
        "scan_subnet": config.scan_subnet,
        "poll_interval_sec": config.poll_interval_sec,
        "snmp_timeout": config.snmp_timeout,
        "snmp_retries": config.snmp_retries,
        "snmp_fast_mode": config.snmp_fast_mode,
        "structure_cache_sec": config.structure_cache_sec,
        "prefetch_concurrency": config.prefetch_concurrency,
    }
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False)
