"""Export/import of switch-list + SNMP settings scenarios."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from swi_mgmt.config import (
    AppConfig,
    SwitchConfig,
    apply_switch_order,
    normalize_switch_order,
)
from swi_mgmt.snmp.v3 import normalize_auth_proto, normalize_priv_proto

SCENARIO_FORMAT = "swi-mgmt-scenario"
SCENARIO_VERSION = 1

ImportMode = Literal["replace", "merge"]

_SETTING_KEYS = (
    "switch_order",
    "scan_community",
    "scan_version",
    "scan_subnet",
    "poll_interval_sec",
    "snmp_timeout",
    "snmp_retries",
    "snmp_fast_mode",
    "structure_cache_sec",
    "prefetch_concurrency",
)


class ScenarioError(ValueError):
    """Invalid scenario payload."""


def export_scenario(config: AppConfig, *, name: str = "") -> dict[str, Any]:
    """Build a portable scenario document from the current app config."""
    settings = {key: getattr(config, key) for key in _SETTING_KEYS}
    settings["switch_order"] = normalize_switch_order(settings.get("switch_order"))
    return {
        "format": SCENARIO_FORMAT,
        "version": SCENARIO_VERSION,
        "name": (name or "").strip(),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "switches": [
            {
                "host": s.host,
                "community": s.community,
                "snmp_version": int(s.snmp_version),
                "name": s.name,
                "driver_id": s.driver_id,
                "port": int(s.port),
                "v3_user": s.v3_user,
                "v3_auth_proto": s.v3_auth_proto,
                "v3_auth_key": s.v3_auth_key,
                "v3_priv_proto": s.v3_priv_proto,
                "v3_priv_key": s.v3_priv_key,
            }
            for s in config.switches
        ],
        "settings": settings,
    }


def _parse_switch(raw: object) -> SwitchConfig:
    if not isinstance(raw, dict):
        raise ScenarioError("Each switch must be an object")
    host = str(raw.get("host", "")).strip()
    if not host:
        raise ScenarioError("Switch entry missing host")
    try:
        snmp_version = int(raw.get("snmp_version", 2))
    except (TypeError, ValueError) as exc:
        raise ScenarioError(f"Invalid snmp_version for {host}") from exc
    if snmp_version not in (1, 2, 3):
        raise ScenarioError(f"snmp_version for {host} must be 1, 2, or 3")
    try:
        port = int(raw.get("port", 161))
    except (TypeError, ValueError) as exc:
        raise ScenarioError(f"Invalid port for {host}") from exc
    if port < 1 or port > 65535:
        raise ScenarioError(f"Port for {host} out of range")
    v3_user = str(raw.get("v3_user", "") or "")
    v3_auth_proto = normalize_auth_proto(str(raw.get("v3_auth_proto", "sha") or "sha"))
    v3_auth_key = str(raw.get("v3_auth_key", "") or "")
    v3_priv_proto = normalize_priv_proto(str(raw.get("v3_priv_proto", "aes128") or "aes128"))
    v3_priv_key = str(raw.get("v3_priv_key", "") or "")
    if snmp_version == 3 and not v3_user.strip():
        raise ScenarioError(f"SNMPv3 user required for {host}")
    return SwitchConfig(
        host=host,
        community=str(raw.get("community", "public")),
        snmp_version=snmp_version,
        name=str(raw.get("name", "") or ""),
        driver_id=str(raw.get("driver_id", "") or ""),
        port=port,
        v3_user=v3_user,
        v3_auth_proto=v3_auth_proto,
        v3_auth_key=v3_auth_key,
        v3_priv_proto=v3_priv_proto,
        v3_priv_key=v3_priv_key,
    )


def _apply_settings(config: AppConfig, settings: dict[str, Any]) -> None:
    if "switch_order" in settings:
        config.switch_order = normalize_switch_order(settings.get("switch_order"))
    if "scan_community" in settings and settings["scan_community"] is not None:
        config.scan_community = str(settings["scan_community"])
    if "scan_version" in settings and settings["scan_version"] is not None:
        try:
            ver = int(settings["scan_version"])
        except (TypeError, ValueError) as exc:
            raise ScenarioError("settings.scan_version must be 1 or 2") from exc
        if ver not in (1, 2):
            raise ScenarioError("settings.scan_version must be 1 or 2")
        config.scan_version = ver
    if "scan_subnet" in settings and settings["scan_subnet"] is not None:
        config.scan_subnet = str(settings["scan_subnet"])
    for key, caster in (
        ("poll_interval_sec", float),
        ("snmp_timeout", float),
        ("structure_cache_sec", float),
    ):
        if key in settings and settings[key] is not None:
            try:
                setattr(config, key, caster(settings[key]))
            except (TypeError, ValueError) as exc:
                raise ScenarioError(f"settings.{key} must be a number") from exc
    if "snmp_retries" in settings and settings["snmp_retries"] is not None:
        try:
            config.snmp_retries = int(settings["snmp_retries"])
        except (TypeError, ValueError) as exc:
            raise ScenarioError("settings.snmp_retries must be an integer") from exc
    if "prefetch_concurrency" in settings and settings["prefetch_concurrency"] is not None:
        try:
            config.prefetch_concurrency = max(1, int(settings["prefetch_concurrency"]))
        except (TypeError, ValueError) as exc:
            raise ScenarioError("settings.prefetch_concurrency must be an integer") from exc
    if "snmp_fast_mode" in settings and settings["snmp_fast_mode"] is not None:
        config.snmp_fast_mode = bool(settings["snmp_fast_mode"])


def parse_scenario(data: object) -> tuple[list[SwitchConfig], dict[str, Any], str]:
    """
    Validate a scenario document.

    Returns (switches, settings_dict, name).
    Accepts both the full scenario envelope and a bare AppConfig-like object
    with a top-level ``switches`` list.
    """
    if not isinstance(data, dict):
        raise ScenarioError("Scenario must be a JSON object")

    fmt = data.get("format")
    if fmt is not None and fmt != SCENARIO_FORMAT:
        raise ScenarioError(f"Unsupported scenario format: {fmt!r}")

    version = data.get("version", SCENARIO_VERSION)
    try:
        version_n = int(version)
    except (TypeError, ValueError) as exc:
        raise ScenarioError("scenario version must be an integer") from exc
    if version_n > SCENARIO_VERSION:
        raise ScenarioError(
            f"Scenario version {version_n} is newer than this app supports ({SCENARIO_VERSION})"
        )

    raw_switches = data.get("switches")
    if not isinstance(raw_switches, list):
        raise ScenarioError("Scenario must include a switches array")

    switches = [_parse_switch(item) for item in raw_switches]
    # Deduplicate by host (last wins) while preserving order of first appearance keys.
    by_host: dict[str, SwitchConfig] = {}
    order: list[str] = []
    for sw in switches:
        if sw.host not in by_host:
            order.append(sw.host)
        by_host[sw.host] = sw
    switches = [by_host[h] for h in order]

    settings_raw = data.get("settings")
    if settings_raw is None:
        # Bare config-style documents may keep settings at the top level.
        settings = {k: data[k] for k in _SETTING_KEYS if k in data}
    elif isinstance(settings_raw, dict):
        settings = {k: settings_raw[k] for k in _SETTING_KEYS if k in settings_raw}
    else:
        raise ScenarioError("settings must be an object")

    name = str(data.get("name", "") or "")
    return switches, settings, name


def apply_scenario(
    config: AppConfig,
    data: object,
    *,
    mode: ImportMode = "replace",
) -> tuple[AppConfig, dict[str, Any]]:
    """
    Apply a scenario onto config in-place.

    Returns (config, summary) where summary has switch counts and mode.
    """
    if mode not in ("replace", "merge"):
        raise ScenarioError("mode must be 'replace' or 'merge'")

    switches, settings, name = parse_scenario(data)
    before = len(config.switches)

    if mode == "replace":
        config.switches = list(switches)
        added = len(switches)
        updated = 0
        removed = max(0, before - len(switches))
    else:
        by_host = {s.host: i for i, s in enumerate(config.switches)}
        added = 0
        updated = 0
        for sw in switches:
            idx = by_host.get(sw.host)
            if idx is None:
                config.switches.append(sw)
                by_host[sw.host] = len(config.switches) - 1
                added += 1
            else:
                config.switches[idx] = sw
                updated += 1
        removed = 0

    _apply_settings(config, settings)
    apply_switch_order(config)

    summary = {
        "mode": mode,
        "name": name,
        "switches": len(config.switches),
        "imported": len(switches),
        "added": added,
        "updated": updated,
        "removed": removed,
    }
    return config, summary
