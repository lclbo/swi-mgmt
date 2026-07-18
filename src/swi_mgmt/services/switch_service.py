"""High-level switch operations used by the UI."""

from __future__ import annotations

import logging
from typing import Literal, Optional

from pysnmp.hlapi.asyncio import SnmpEngine

from swi_mgmt.config import SwitchConfig
from swi_mgmt.drivers.registry import create_driver
from swi_mgmt.models.switch import SwitchSnapshot
from swi_mgmt.snmp.client import SnmpClient, SnmpError
from swi_mgmt.snmp.scanner import ScanResult, get_local_subnet, scan_subnet, suggest_scan_cidr

logger = logging.getLogger(__name__)

SnapshotMode = Literal["full", "live", "fast"]


async def fetch_snapshot(
    config: SwitchConfig,
    prev_counters: Optional[dict] = None,
    timeout: float = 8.0,
    retries: int = 2,
    engine: SnmpEngine | None = None,
    mode: SnapshotMode = "full",
    prior: SwitchSnapshot | None = None,
) -> SwitchSnapshot:
    """Connect to a switch and return a snapshot (full / fast / live)."""
    client = SnmpClient(
        config.host,
        config.community,
        config.snmp_version,
        port=config.port,
        timeout=timeout,
        retries=retries,
        engine=engine,
        v3_user=config.v3_user,
        v3_auth_proto=config.v3_auth_proto,
        v3_auth_key=config.v3_auth_key,
        v3_priv_proto=config.v3_priv_proto,
        v3_priv_key=config.v3_priv_key,
    )
    try:
        sys_info = await client.get_many(
            ["1.3.6.1.2.1.1.1.0", "1.3.6.1.2.1.1.2.0"]
        )
        sys_descr = str(sys_info.get("1.3.6.1.2.1.1.1.0", ""))
        sys_oid = str(sys_info.get("1.3.6.1.2.1.1.2.0", ""))
        driver = create_driver(client, sys_descr, sys_oid, config.driver_id or None)
        return await driver.get_snapshot(
            prev_counters,
            mode=mode,
            prior_ports=prior.ports if prior else None,
            prior_vlans=prior.vlans if prior else None,
            prior_identity=prior.identity if prior else None,
        )
    finally:
        client.close()


async def probe_switch(config: SwitchConfig, timeout: float = 3.0) -> Optional[SwitchSnapshot]:
    """Quick probe: return snapshot or None on failure."""
    try:
        return await fetch_snapshot(config, timeout=timeout)
    except (SnmpError, OSError, TimeoutError) as exc:
        logger.debug("Probe failed for %s: %s", config.host, exc)
        return None


async def run_scan(
    cidr: str,
    community: str = "public",
    version: int = 2,
    timeout: float = 1.5,
    progress_callback=None,
    cancel_event=None,
    communities: list[str] | None = None,
    include_icmp_only: bool = True,
) -> list[ScanResult]:
    if not cidr:
        cidr = suggest_scan_cidr()
    return await scan_subnet(
        cidr,
        community,
        version,
        timeout,
        progress_callback=progress_callback,
        cancel_event=cancel_event,
        communities=communities,
        include_icmp_only=include_icmp_only,
    )
