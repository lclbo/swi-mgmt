"""Abstract base class for switch vendor drivers."""

from __future__ import annotations

import abc
import time
from typing import Literal, Optional

from swi_mgmt.models.switch import (
    PortStatus,
    SwitchIdentity,
    SwitchSnapshot,
    VlanInfo,
)
from swi_mgmt.snmp.client import SnmpClient

SnapshotMode = Literal["full", "live", "fast"]


class SwitchDriver(abc.ABC):
    """Vendor/series-specific SNMP driver for read-only switch monitoring."""

    driver_id: str = "generic"
    display_name: str = "Generic SNMP Switch"
    description: str = "Standard MIB-II + Q-BRIDGE-MIB driver"

    def __init__(self, client: SnmpClient) -> None:
        self.client = client

    @classmethod
    @abc.abstractmethod
    def matches(cls, sys_descr: str, sys_object_id: str = "") -> bool:
        """Return True if this driver supports the given device."""

    @abc.abstractmethod
    async def get_identity(self) -> SwitchIdentity:
        """Retrieve switch identity information."""

    @abc.abstractmethod
    async def get_vlans(self) -> list[VlanInfo]:
        """Retrieve all configured VLANs."""

    @abc.abstractmethod
    async def get_ports(
        self,
        prev_counters: Optional[dict[int, tuple[int, int]]] = None,
        *,
        include_counters: bool = True,
    ) -> list[PortStatus]:
        """Retrieve port status, optionally computing rates from previous counters."""

    async def refresh_live_counters(
        self,
        ports: list[PortStatus],
        prev_counters: Optional[dict[int, tuple[int, int, float]]] = None,
    ) -> list[PortStatus]:
        """Update oper/counters on an existing port list (structure unchanged)."""
        return ports

    async def get_snapshot(
        self,
        prev_counters: Optional[dict[int, tuple[int, int, float]]] = None,
        *,
        mode: SnapshotMode = "full",
        prior_ports: Optional[list[PortStatus]] = None,
        prior_vlans: Optional[list[VlanInfo]] = None,
        prior_identity: Optional[SwitchIdentity] = None,
    ) -> SwitchSnapshot:
        """Build a switch snapshot.

        Modes:
        - full: identity + VLANs + ports + counters
        - fast: identity + VLANs + ports, skip traffic counters
        - live: reuse prior structure; refresh oper/counters only
        """
        now = time.monotonic()

        if (
            mode == "live"
            and prior_ports
            and prior_vlans is not None
            and prior_identity is not None
        ):
            ports = await self.refresh_live_counters(prior_ports, prev_counters)
            if prev_counters:
                for port in ports:
                    if port.index in prev_counters:
                        prev_in, prev_out, prev_time = prev_counters[port.index]
                        elapsed = now - prev_time
                        if elapsed > 0:
                            port.in_rate_bps = max(0, (port.in_octets - prev_in) * 8 / elapsed)
                            port.out_rate_bps = max(0, (port.out_octets - prev_out) * 8 / elapsed)
            return SwitchSnapshot(
                identity=prior_identity,
                vlans=prior_vlans,
                ports=ports,
                timestamp=now,
            )

        identity = await self.get_identity()
        vlans = await self.get_vlans()
        include_counters = mode != "fast"

        prev: Optional[dict[int, tuple[int, int]]] = None
        prev_time: Optional[float] = None
        if prev_counters and include_counters:
            prev = {k: (v[0], v[1]) for k, v in prev_counters.items()}
            prev_time = next(iter(prev_counters.values()))[2]

        ports = await self.get_ports(prev, include_counters=include_counters)

        # Enrich VLAN list with IDs seen on ports (avoids a second egress walk in get_vlans).
        vlan_by_id = {v.vlan_id: v for v in vlans}
        for port in ports:
            for vid in (*port.untagged_vlans, *port.tagged_vlans, port.primary_vlan):
                if vid and vid not in vlan_by_id:
                    vlan_by_id[vid] = VlanInfo(vlan_id=vid)
        vlans = sorted(vlan_by_id.values(), key=lambda v: v.vlan_id)

        if prev_counters and prev_time and include_counters:
            elapsed = now - prev_time
            if elapsed > 0:
                for port in ports:
                    if port.index in prev_counters:
                        prev_in, prev_out, _ = prev_counters[port.index]
                        port.in_rate_bps = max(0, (port.in_octets - prev_in) * 8 / elapsed)
                        port.out_rate_bps = max(0, (port.out_octets - prev_out) * 8 / elapsed)

        return SwitchSnapshot(
            identity=identity,
            vlans=vlans,
            ports=ports,
            timestamp=now,
        )

    def is_physical_port(self, if_type: int, if_descr: str) -> bool:
        """Filter out virtual/management interfaces."""
        descr_lower = if_descr.lower().strip()
        if any(
            kw in descr_lower
            for kw in (
                "vlan",
                "loopback",
                "null",
                "tunnel",
                "aux",
                "cpu",
                "stack",
                "lag",
                "port-channel",
                "portchannel",
                "aggregate",
                "routing",
            )
        ):
            return False
        physical_types = {6, 62, 117, 135, 136}
        if if_type in physical_types:
            return True
        if any(kw in descr_lower for kw in ("gigabit", "fast ethernet", "10g", "port", "ge")):
            return True
        if descr_lower.startswith(("1/", "2/", "gi", "fa", "te")):
            return True
        return False
