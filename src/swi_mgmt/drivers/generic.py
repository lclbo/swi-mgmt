"""Generic SNMP switch driver using standard MIB-II and Q-BRIDGE-MIB."""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from typing import Optional

from swi_mgmt.drivers.base import SwitchDriver
from swi_mgmt.models.switch import (
    IfAdminStatus,
    IfOperStatus,
    PoePortStatus,
    PortStatus,
    SwitchIdentity,
    VlanInfo,
)
from swi_mgmt.snmp import oids
from swi_mgmt.snmp.media_mode import (
    media_mode_from_if_descr,
    parse_mau_type_walk,
    resolve_media_mode,
)
from swi_mgmt.snmp.poe import fetch_poe_by_ifindex
from swi_mgmt.snmp.portlist import portlist_to_indices

logger = logging.getLogger(__name__)


class GenericSnmpDriver(SwitchDriver):
    """Standard MIB-II + Q-BRIDGE-MIB driver for VLAN-capable switches."""

    driver_id = "generic"
    display_name = "Generic SNMP Switch"
    description = "Standard Q-BRIDGE-MIB driver for IEEE 802.1Q switches"

    @classmethod
    def matches(cls, sys_descr: str, sys_object_id: str = "") -> bool:
        return True  # fallback driver

    async def get_identity(self) -> SwitchIdentity:
        info = await self.client.get_many([oids.SYS_NAME, oids.SYS_DESCR, oids.SYS_OBJECT_ID])
        sys_name = str(info.get(oids.SYS_NAME, ""))
        sys_descr = str(info.get(oids.SYS_DESCR, ""))
        return SwitchIdentity(
            host=self.client.host,
            sys_name=sys_name,
            sys_descr=sys_descr,
            driver_id=self.driver_id,
        )

    async def get_vlans(self) -> list[VlanInfo]:
        """VLAN names from the static table only (no egress walk — membership does that)."""
        vlans: dict[int, VlanInfo] = {}
        try:
            static_names = await self.client.walk(oids.DOT1Q_VLAN_STATIC_NAME)
            for oid, name in static_names.items():
                vlan_id = int(oid.rsplit(".", 1)[-1])
                vlans[vlan_id] = VlanInfo(vlan_id=vlan_id, name=str(name))
        except Exception as exc:
            logger.debug("Static VLAN walk failed: %s", exc)
        return sorted(vlans.values(), key=lambda v: v.vlan_id)

    async def _walk_vlan_portlists(
        self, egress_oid: str, untagged_oid: str
    ) -> tuple[dict[int, list[int]], dict[int, list[int]]]:
        """Walk an egress/untagged PortList pair, keyed by VLAN ID."""
        egress_data = await self.client.walk(egress_oid)
        untagged_data = await self.client.walk(untagged_oid)

        vlan_egress: dict[int, list[int]] = {}
        for oid, val in egress_data.items():
            vlan_id = int(oid.rsplit(".", 1)[-1])
            vlan_egress[vlan_id] = portlist_to_indices(self._to_bytes(val))

        vlan_untagged: dict[int, list[int]] = {}
        for oid, val in untagged_data.items():
            vlan_id = int(oid.rsplit(".", 1)[-1])
            vlan_untagged[vlan_id] = portlist_to_indices(self._to_bytes(val))

        return vlan_egress, vlan_untagged

    async def _get_vlan_membership(self, num_ports: int) -> tuple[dict[int, list[int]], dict[int, list[int]]]:
        from collections import defaultdict

        untagged: dict[int, list[int]] = defaultdict(list)
        tagged: dict[int, list[int]] = defaultdict(list)

        vlan_egress: dict[int, list[int]] = {}
        vlan_untagged: dict[int, list[int]] = {}

        # Prefer the current table; fall back to the static table if empty.
        try:
            vlan_egress, vlan_untagged = await self._walk_vlan_portlists(
                oids.DOT1Q_VLAN_CURRENT_EGRESS, oids.DOT1Q_VLAN_CURRENT_UNTAGGED
            )
        except Exception as exc:
            logger.debug("Current VLAN membership walk failed: %s", exc)

        if not vlan_egress:
            try:
                vlan_egress, vlan_untagged = await self._walk_vlan_portlists(
                    oids.DOT1Q_VLAN_STATIC_EGRESS, oids.DOT1Q_VLAN_STATIC_UNTAGGED
                )
            except Exception as exc:
                logger.debug("Static VLAN membership walk failed: %s", exc)

        # Keep PortList indices as-is (may be sparse: e.g. 1–24 and 49–52).
        # num_ports is only a soft hint for ignoring stack/lag ghosts.
        max_keep = max(num_ports, 512)
        for vlan_id, egress_ports in vlan_egress.items():
            untagged_ports = set(vlan_untagged.get(vlan_id, []))
            for port_idx in egress_ports:
                if port_idx < 1 or port_idx > max_keep:
                    continue
                if port_idx in untagged_ports:
                    untagged[port_idx].append(vlan_id)
                else:
                    tagged[port_idx].append(vlan_id)

        return dict(untagged), dict(tagged)

    async def _get_pvid_map(self) -> dict[int, int]:
        """Return bridge port index -> PVID."""
        pvid_map: dict[int, int] = {}
        try:
            data = await self.client.walk(oids.DOT1Q_PVID)
            for oid, val in data.items():
                port_idx = int(oid.rsplit(".", 1)[-1])
                pvid_map[port_idx] = int(val)
        except Exception as exc:
            logger.debug("PVID walk failed: %s", exc)
        return pvid_map

    def _vlan_bridge_index(
        self,
        ifindex: int,
        port_name: str,
        ifindex_to_bridge: dict[int, int],
    ) -> int:
        """Map an interface to the bridge port index used in Q-BRIDGE PortLists."""
        return ifindex_to_bridge.get(ifindex, ifindex)

    def _resolve_primary_vlan(
        self,
        vlan_idx: int,
        pvid_map: dict[int, int],
        untagged: list[int],
    ) -> int:
        """Choose the port's display PVID / native VLAN."""
        primary = pvid_map.get(vlan_idx, 0)
        if primary > 0:
            return primary
        if len(untagged) == 1:
            return untagged[0]
        if untagged:
            return untagged[0]
        return 1

    async def get_ports(
        self,
        prev_counters: Optional[dict[int, tuple[int, int]]] = None,
        *,
        include_counters: bool = True,
    ) -> list[PortStatus]:
        if_descr = await self.client.walk(oids.IF_DESCR)
        if_type = await self.client.walk(oids.IF_TYPE)
        if_oper = await self.client.walk(oids.IF_OPER_STATUS)
        if_admin = await self.client.walk(oids.IF_ADMIN_STATUS)

        # Prefer ifHighSpeed; only fall back to ifSpeed when needed.
        if_high_speed = await self.client.walk(oids.IF_HIGH_SPEED)
        if_speed: dict[str, object] = {}
        need_speed_fallback = any(
            int(if_high_speed.get(f"{oids.IF_HIGH_SPEED}.{int(oid.rsplit('.', 1)[-1])}", 0) or 0) == 0
            for oid in if_descr
        )
        if need_speed_fallback:
            if_speed = await self.client.walk(oids.IF_SPEED)

        if_hc_in: dict[str, object] = {}
        if_hc_out: dict[str, object] = {}
        if_in: dict[str, object] = {}
        if_out: dict[str, object] = {}
        if include_counters:
            # Prefer 64-bit HC counters; skip 32-bit walks when HC works.
            try:
                if_hc_in = await self.client.walk(oids.IF_HC_IN_OCTETS)
                if_hc_out = await self.client.walk(oids.IF_HC_OUT_OCTETS)
            except Exception:
                pass
            if not if_hc_in and not if_hc_out:
                if_in = await self.client.walk(oids.IF_IN_OCTETS)
                if_out = await self.client.walk(oids.IF_OUT_OCTETS)

        # Bridge port to ifIndex mapping
        bridge_map: dict[int, int] = {}
        try:
            bp_data = await self.client.walk(oids.DOT1D_BASE_PORT_IF_INDEX)
            for oid, if_idx in bp_data.items():
                bp = int(oid.rsplit(".", 1)[-1])
                bridge_map[bp] = int(if_idx)
        except Exception:
            pass

        ifindex_to_bridge = {v: k for k, v in bridge_map.items()}
        num_bridge_ports = max(bridge_map.keys()) if bridge_map else len(if_descr)

        pvid_map = await self._get_pvid_map()
        untagged_map, tagged_map = await self._get_vlan_membership(num_bridge_ports)

        mau_modes: dict[int, str] = {}
        try:
            mau_modes = parse_mau_type_walk(await self.client.walk(oids.IF_MAU_TYPE))
        except Exception:
            logger.debug("ifMauType walk unavailable", exc_info=True)

        poe_by_index: dict[int, PoePortStatus] = {}
        try:
            poe_by_index = await fetch_poe_by_ifindex(self.client)
        except Exception:
            logger.debug("PoE walk unavailable", exc_info=True)

        ports: list[PortStatus] = []
        for oid, descr in if_descr.items():
            idx = int(oid.rsplit(".", 1)[-1])
            type_oid = f"{oids.IF_TYPE}.{idx}"
            itype = int(if_type.get(type_oid, 0))
            descr_str = str(descr)

            if not self.is_physical_port(itype, descr_str):
                continue

            oper = IfOperStatus(int(if_oper.get(f"{oids.IF_OPER_STATUS}.{idx}", 2)))
            try:
                admin = IfAdminStatus(int(if_admin.get(f"{oids.IF_ADMIN_STATUS}.{idx}", 1)))
            except ValueError:
                admin = IfAdminStatus.UP

            in_oct = 0
            out_oct = 0
            if include_counters:
                in_oct = int(if_hc_in.get(f"{oids.IF_HC_IN_OCTETS}.{idx}", 0) or 0)
                out_oct = int(if_hc_out.get(f"{oids.IF_HC_OUT_OCTETS}.{idx}", 0) or 0)
                if in_oct == 0 and if_in:
                    in_oct = int(if_in.get(f"{oids.IF_IN_OCTETS}.{idx}", 0) or 0)
                if out_oct == 0 and if_out:
                    out_oct = int(if_out.get(f"{oids.IF_OUT_OCTETS}.{idx}", 0) or 0)

            high_speed = int(if_high_speed.get(f"{oids.IF_HIGH_SPEED}.{idx}", 0) or 0)
            if high_speed:
                speed_mbps = high_speed
            else:
                speed = int(if_speed.get(f"{oids.IF_SPEED}.{idx}", 0) or 0)
                speed_mbps = speed // 1_000_000 if speed else None

            bridge_idx = self._vlan_bridge_index(idx, descr_str, ifindex_to_bridge)
            untagged = sorted(untagged_map.get(bridge_idx, []))
            tagged = sorted(tagged_map.get(bridge_idx, []))
            primary = self._resolve_primary_vlan(bridge_idx, pvid_map, untagged)

            # Active combo/media side is only meaningful while the link is up.
            media_mode = resolve_media_mode(
                oper_up=oper == IfOperStatus.UP,
                mau_mode=mau_modes.get(idx),
                descr_mode=media_mode_from_if_descr(descr_str),
            )

            poe = poe_by_index.get(idx) or poe_by_index.get(bridge_idx)

            ports.append(
                PortStatus(
                    index=idx,
                    name=descr_str,
                    admin_status=admin,
                    oper_status=oper,
                    speed_mbps=speed_mbps,
                    in_octets=in_oct,
                    out_octets=out_oct,
                    primary_vlan=primary,
                    untagged_vlans=untagged,
                    tagged_vlans=tagged,
                    media_mode=media_mode,
                    poe=poe,
                )
            )

        ports.sort(key=lambda p: _natural_sort_key(p.name))
        return ports

    async def refresh_live_counters(
        self,
        ports: list[PortStatus],
        prev_counters: Optional[dict[int, tuple[int, int, float]]] = None,
    ) -> list[PortStatus]:
        """Refresh oper status and traffic counters without re-walking VLANs/structure."""
        if not ports:
            return ports

        if_oper = await self.client.walk(oids.IF_OPER_STATUS)
        if_admin = await self.client.walk(oids.IF_ADMIN_STATUS)
        if_hc_in: dict[str, object] = {}
        if_hc_out: dict[str, object] = {}
        if_in: dict[str, object] = {}
        if_out: dict[str, object] = {}
        try:
            if_hc_in = await self.client.walk(oids.IF_HC_IN_OCTETS)
            if_hc_out = await self.client.walk(oids.IF_HC_OUT_OCTETS)
        except Exception:
            pass
        if not if_hc_in and not if_hc_out:
            if_in = await self.client.walk(oids.IF_IN_OCTETS)
            if_out = await self.client.walk(oids.IF_OUT_OCTETS)

        mau_modes: dict[int, str] = {}
        try:
            mau_modes = parse_mau_type_walk(await self.client.walk(oids.IF_MAU_TYPE))
        except Exception:
            pass

        poe_by_index: dict[int, PoePortStatus] = {}
        try:
            poe_by_index = await fetch_poe_by_ifindex(self.client)
        except Exception:
            pass

        updated: list[PortStatus] = []
        for port in ports:
            idx = port.index
            oper = IfOperStatus(int(if_oper.get(f"{oids.IF_OPER_STATUS}.{idx}", port.oper_status)))
            try:
                admin = IfAdminStatus(
                    int(if_admin.get(f"{oids.IF_ADMIN_STATUS}.{idx}", port.admin_status))
                )
            except (ValueError, TypeError):
                admin = port.admin_status
            in_oct = int(if_hc_in.get(f"{oids.IF_HC_IN_OCTETS}.{idx}", 0) or 0)
            out_oct = int(if_hc_out.get(f"{oids.IF_HC_OUT_OCTETS}.{idx}", 0) or 0)
            if in_oct == 0 and if_in:
                in_oct = int(if_in.get(f"{oids.IF_IN_OCTETS}.{idx}", 0) or 0)
            if out_oct == 0 and if_out:
                out_oct = int(if_out.get(f"{oids.IF_OUT_OCTETS}.{idx}", 0) or 0)
            if not if_hc_in and not if_in:
                in_oct = port.in_octets
            if not if_hc_out and not if_out:
                out_oct = port.out_octets
            media_mode = resolve_media_mode(
                oper_up=oper == IfOperStatus.UP,
                mau_mode=mau_modes.get(idx) or port.media_mode,
                descr_mode=media_mode_from_if_descr(port.name),
            )
            poe = poe_by_index.get(idx, port.poe)
            updated.append(
                replace(
                    port,
                    admin_status=admin,
                    oper_status=oper,
                    in_octets=in_oct,
                    out_octets=out_oct,
                    media_mode=media_mode,
                    poe=poe,
                )
            )
        return updated

    @staticmethod
    def _to_bytes(val: object) -> bytes:
        if isinstance(val, bytes):
            return val
        if isinstance(val, str):
            return val.encode("latin-1")
        return bytes(str(val), "latin-1")


def _natural_sort_key(name: str) -> tuple:
    """Sort port names naturally (e.g. port 2 before port 10)."""
    parts = re.split(r"(\d+)", name)
    return tuple(int(p) if p.isdigit() else p.lower() for p in parts)
