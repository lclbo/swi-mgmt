"""Parse POWER-ETHERNET-MIB (and optional vendor) PoE port status."""

from __future__ import annotations

import logging
from typing import Optional

from swi_mgmt.models.switch import PoePortStatus
from swi_mgmt.snmp import oids
from swi_mgmt.snmp.client import SnmpClient

logger = logging.getLogger(__name__)

_DETECTION = {
    1: "disabled",
    2: "searching",
    3: "delivering",
    4: "fault",
    5: "test",
    6: "otherFault",
}

_CLASS = {
    1: 0,
    2: 1,
    3: 2,
    4: 3,
    5: 4,
}

_PRIORITY = {
    1: "critical",
    2: "high",
    3: "low",
}


def _port_index_from_oid(oid: str, base: str) -> Optional[int]:
    """Extract pethPsePortIndex from a group.port suffix (last component)."""
    if not oid.startswith(base + "."):
        return None
    suffix = oid[len(base) + 1 :]
    parts = suffix.split(".")
    if not parts:
        return None
    try:
        return int(parts[-1])
    except ValueError:
        return None


def _truth_enable(val: object) -> Optional[bool]:
    try:
        n = int(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if n == 1:
        return True
    if n == 2:
        return False
    return None


def _int_or_none(val: object) -> Optional[int]:
    try:
        return int(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _merge_column(
    by_port: dict[int, dict],
    walk: dict[str, object],
    base: str,
    key: str,
) -> None:
    for oid, val in walk.items():
        idx = _port_index_from_oid(oid, base)
        if idx is None:
            continue
        by_port.setdefault(idx, {})[key] = val


async def fetch_poe_by_ifindex(client: SnmpClient) -> dict[int, PoePortStatus]:
    """
    Walk PoE tables and return status keyed by interface / port index.

    Returns an empty dict when the device has no POWER-ETHERNET-MIB support
    (non-PoE switches, or SNMP denied).
    """
    by_port: dict[int, dict] = {}
    try:
        admin = await client.walk(oids.PETH_PSE_PORT_ADMIN_ENABLE)
    except Exception:
        logger.debug("PoE admin walk unavailable", exc_info=True)
        return {}
    if not admin:
        return {}

    _merge_column(by_port, admin, oids.PETH_PSE_PORT_ADMIN_ENABLE, "admin")

    for base, key in (
        (oids.PETH_PSE_PORT_DETECTION_STATUS, "detection"),
        (oids.PETH_PSE_PORT_POWER_PRIORITY, "priority"),
        (oids.PETH_PSE_PORT_POWER_CLASSIFICATIONS, "classification"),
    ):
        try:
            _merge_column(by_port, await client.walk(base), base, key)
        except Exception:
            logger.debug("PoE %s walk unavailable", key, exc_info=True)

    # Prefer actual draw; fall back to supplied power (both milliwatts).
    for base in (oids.HPICF_POE_PORT_ACTUAL_POWER, oids.HPICF_POE_PORT_POWER):
        try:
            walk = await client.walk(base)
        except Exception:
            continue
        if not walk:
            continue
        for oid, val in walk.items():
            idx = _port_index_from_oid(oid, base)
            if idx is None:
                continue
            mw = _int_or_none(val)
            if mw is None:
                continue
            slot = by_port.setdefault(idx, {})
            if "power_mw" not in slot or base == oids.HPICF_POE_PORT_ACTUAL_POWER:
                slot["power_mw"] = mw
        break

    result: dict[int, PoePortStatus] = {}
    for idx, raw in by_port.items():
        admin_en = _truth_enable(raw.get("admin"))
        det_n = _int_or_none(raw.get("detection"))
        detection = _DETECTION.get(det_n) if det_n is not None else None
        class_n = _int_or_none(raw.get("classification"))
        power_class = _CLASS.get(class_n) if class_n is not None else None
        pri_n = _int_or_none(raw.get("priority"))
        priority = _PRIORITY.get(pri_n) if pri_n is not None else None
        power_mw = _int_or_none(raw.get("power_mw"))
        delivering = detection == "delivering"
        result[idx] = PoePortStatus(
            admin_enable=admin_en,
            detection=detection,
            delivering=delivering,
            power_mw=power_mw,
            power_class=power_class,
            priority=priority,
        )
    return result
