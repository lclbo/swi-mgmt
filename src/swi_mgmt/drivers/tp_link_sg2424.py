"""TP-Link JetStream SG2424 / T1600G-28TS switch driver."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Optional

from swi_mgmt.drivers.generic import GenericSnmpDriver
from swi_mgmt.models.switch import IfOperStatus, PortStatus, SwitchIdentity
from swi_mgmt.snmp.media_mode import media_mode_from_if_descr, resolve_media_mode

# TP-Link enterprise OID prefix (numeric or pysnmp pretty form)
_TPLINK_ENTERPRISE_RE = re.compile(
    r"(?:^|\.)1\.3\.6\.1\.4\.1\.11863\b|enterprises\.11863\b",
    re.I,
)

# Front-panel combo ports on classic 24G+4 combo faces (panels 21–24).
_COMBO_PANELS = frozenset({21, 22, 23, 24})


def panel_from_descr(if_descr: str) -> int | None:
    """Extract front-panel port number from TP-Link-style ifDescr."""
    descr = if_descr.strip()
    m = re.match(r"^port[\s_-]*(\d+)\b", descr, re.I)
    if m:
        return int(m.group(1))
    # "gigabit copper 21", "Gigabit Fiber 22", "gigabit ethernet 3"
    m = re.search(
        r"(?:gigabit|ten-?gigabit|fast)?\s*(?:copper|fiber|fibre|ethernet)\s+"
        r"(?:\d+/)*(\d+)\s*$",
        descr,
        re.I,
    )
    if m:
        return int(m.group(1))
    m = re.search(r"^(?:gi|te|fa)\s*(?:\d+/)*(\d+)\s*$", descr, re.I)
    if m:
        return int(m.group(1))
    if re.fullmatch(r"\d+", descr):
        return int(descr)
    return None


def side_from_descr(if_descr: str) -> str | None:
    """Return copper/fiber when ifDescr names the combo side explicitly."""
    return media_mode_from_if_descr(if_descr)


class TpLinkSg2424Driver(GenericSnmpDriver):
    """Driver for TP-Link JetStream 24G + 4 combo/SFP smart switches.

    Covers TL-SG2424 / T1600G-28TS and older SKUs whose sysDescr is only
    ``24-Port Gigabit Smart Switch with 4 Combo SFP Slots``.

    These devices often expose combo ports as paired ifDescr entries
    (``gigabit copper N`` / ``gigabit fiber N``). This driver merges them into
    one panel port and sets ``media_mode`` from the active side.
    """

    driver_id = "tp_link_sg2424"
    display_name = "TP-Link JetStream SG2424"
    description = (
        "TP-Link JetStream 24×RJ45 + 4×combo/SFP (SG2424, T1600G-28TS, "
        "and ‘24-Port Gigabit Smart Switch with 4 Combo SFP Slots’)"
    )

    _MATCH_PATTERNS = [
        re.compile(r"TL-?SG2424", re.I),
        re.compile(r"\bSG2424\b", re.I),
        re.compile(r"T1600G-28TS", re.I),
        re.compile(r"T1600G-28\b", re.I),
        re.compile(r"JetStream.*28TS", re.I),
        re.compile(r"24-Port Gigabit Smart Switch with 4 Combo", re.I),
    ]

    @classmethod
    def matches(cls, sys_descr: str, sys_object_id: str = "") -> bool:
        blob = f"{sys_descr} {sys_object_id}"
        for pattern in cls._MATCH_PATTERNS:
            if pattern.search(blob):
                return True
        if _TPLINK_ENTERPRISE_RE.search(sys_object_id) and re.search(
            r"2424|T1600G-28|28TS|4 Combo|24-Port Gigabit",
            sys_descr,
            re.I,
        ):
            return True
        return False

    async def get_identity(self) -> SwitchIdentity:
        identity = await super().get_identity()
        identity.vendor = "TP-Link"
        identity.model = self._extract_model(identity.sys_descr)
        identity.driver_id = self.driver_id
        return identity

    async def get_ports(
        self,
        prev_counters: Optional[dict[int, tuple[int, int]]] = None,
        *,
        include_counters: bool = True,
    ) -> list[PortStatus]:
        ports = await super().get_ports(
            prev_counters, include_counters=include_counters
        )
        return self._merge_combo_panel_ports(ports)

    async def refresh_live_counters(
        self,
        ports: list[PortStatus],
        prev_counters: Optional[dict[int, tuple[int, int, float]]] = None,
    ) -> list[PortStatus]:
        refreshed = await super().refresh_live_counters(ports, prev_counters)
        return [self._annotate_combo_port(p) for p in refreshed]

    def is_physical_port(self, if_type: int, if_descr: str) -> bool:
        descr = if_descr.strip()
        descr_lower = descr.lower()
        if any(
            kw in descr_lower
            for kw in (
                "vlan",
                "loopback",
                "null",
                "tunnel",
                "port-channel",
                "portchannel",
                "aggregate",
                "lag",
                "cpu",
                "routing",
                "aux",
            )
        ):
            return False
        if panel_from_descr(descr) is not None:
            return if_type in (6, 62, 117)
        if if_type not in (6, 117, 62):
            return False
        return bool(
            re.search(
                r"gigabit|ethernet|copper|fiber|fibre|sfp|\bgi\d|\bte\d|\bport\b",
                descr_lower,
            )
        )

    def _vlan_bridge_index(
        self,
        ifindex: int,
        port_name: str,
        ifindex_to_bridge: dict[int, int],
    ) -> int:
        """TP-Link Q-BRIDGE PortLists use front-panel port numbers."""
        panel = panel_from_descr(port_name)
        if panel is not None:
            return panel
        return ifindex_to_bridge.get(ifindex, ifindex)

    def _merge_combo_panel_ports(self, ports: list[PortStatus]) -> list[PortStatus]:
        """Collapse copper/fiber ifDescr pairs for the same panel into one port."""
        by_panel: dict[int, list[PortStatus]] = {}
        passthrough: list[PortStatus] = []
        for port in ports:
            panel = panel_from_descr(port.name)
            if panel is None:
                passthrough.append(port)
                continue
            by_panel.setdefault(panel, []).append(port)

        merged: list[PortStatus] = []
        for panel in sorted(by_panel):
            group = by_panel[panel]
            if len(group) == 1:
                merged.append(self._annotate_combo_port(group[0], panel=panel))
                continue
            merged.append(self._pick_combo_representative(panel, group))

        merged.extend(passthrough)
        merged.sort(key=lambda p: (panel_from_descr(p.name) or p.index, p.index))
        return merged

    def _pick_combo_representative(
        self, panel: int, group: list[PortStatus]
    ) -> PortStatus:
        fiber = [p for p in group if side_from_descr(p.name) == "fiber"]
        copper = [p for p in group if side_from_descr(p.name) == "copper"]
        other = [
            p for p in group if side_from_descr(p.name) not in ("fiber", "copper")
        ]

        up_fiber = next((p for p in fiber if p.oper_status == IfOperStatus.UP), None)
        up_copper = next((p for p in copper if p.oper_status == IfOperStatus.UP), None)
        up_other = next((p for p in other if p.oper_status == IfOperStatus.UP), None)

        if up_fiber is not None:
            chosen, mode = up_fiber, "fiber"
        elif up_copper is not None:
            chosen, mode = up_copper, "copper"
        elif up_other is not None:
            chosen = up_other
            mode = resolve_media_mode(
                oper_up=True,
                mau_mode=up_other.media_mode,
                descr_mode=side_from_descr(up_other.name),
            )
        else:
            # Link down: keep combo designator (no media_mode).
            chosen = (copper or fiber or other or group)[0]
            mode = None

        return replace(
            chosen,
            name=self._combo_display_name(panel, mode),
            media_mode=mode,
        )

    def _annotate_combo_port(
        self, port: PortStatus, panel: int | None = None
    ) -> PortStatus:
        panel = panel if panel is not None else panel_from_descr(port.name)
        synthetic = bool(
            re.match(r"^Port\s+\d+\s+\((?:combo|copper|fiber)\)$", port.name, re.I)
        )
        is_combo = panel in _COMBO_PANELS or (
            (side_from_descr(port.name) is not None and panel is not None) or synthetic
        )
        oper_up = port.oper_status == IfOperStatus.UP
        # Don't infer side from our own "Port N (fiber)" labels on live refresh.
        descr_mode = None if synthetic else side_from_descr(port.name)
        mode = resolve_media_mode(
            oper_up=oper_up,
            mau_mode=port.media_mode,
            descr_mode=descr_mode,
        )
        # Idle combo ports are often still named "gigabit copper" — do not treat
        # that label as an active-side determination when the link is down.
        if not oper_up and is_combo:
            mode = None
        if panel is not None and is_combo:
            return replace(
                port,
                name=self._combo_display_name(panel, mode),
                media_mode=mode,
            )
        return replace(port, media_mode=mode)

    @staticmethod
    def _combo_display_name(panel: int, mode: str | None) -> str:
        if mode == "fiber":
            return f"Port {panel} (fiber)"
        if mode == "copper":
            return f"Port {panel} (copper)"
        return f"Port {panel} (combo)"

    @staticmethod
    def _extract_model(sys_descr: str) -> str:
        for pattern in (
            r"(TL-?SG2424\S*)",
            r"(T1600G-28TS\S*)",
            r"(T1600G-28\S*)",
            r"(SG2424\S*)",
        ):
            match = re.search(pattern, sys_descr, re.I)
            if match:
                return match.group(1).strip()
        if re.search(r"4 Combo", sys_descr, re.I):
            return "24-Port Gigabit Smart Switch with 4 Combo SFP Slots"
        return "SG2424 / T1600G-28TS"
