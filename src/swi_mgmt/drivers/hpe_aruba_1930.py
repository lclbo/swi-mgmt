"""HPE / Aruba Instant On 1930 series switch driver."""

from __future__ import annotations

import re

from swi_mgmt.drivers.generic import GenericSnmpDriver
from swi_mgmt.models.switch import SwitchIdentity


class HpeAruba1930Driver(GenericSnmpDriver):
    """Driver for HPE / Aruba Instant On 1930 series smart-managed switches."""

    driver_id = "hpe_aruba_1930"
    display_name = "HPE / Aruba Instant On 1930"
    description = "Aruba Instant On 1930 series (JL680A, JL681A, JL682A, JL683A, JL684A, JL685A, etc.)"

    _MATCH_PATTERNS = [
        re.compile(r"aruba.*1930", re.I),
        re.compile(r"instant\s*on.*1930", re.I),
        re.compile(r"JL68[0-9]A", re.I),
        re.compile(r"HP.*1930", re.I),
        re.compile(r"HPE.*1930", re.I),
    ]

    @classmethod
    def matches(cls, sys_descr: str, sys_object_id: str = "") -> bool:
        for pattern in cls._MATCH_PATTERNS:
            if pattern.search(sys_descr):
                return True
        return False

    async def get_identity(self) -> SwitchIdentity:
        identity = await super().get_identity()
        identity.vendor = "HPE / Aruba"
        identity.model = self._extract_model(identity.sys_descr)
        identity.driver_id = self.driver_id
        return identity

    def is_physical_port(self, if_type: int, if_descr: str) -> bool:
        descr = if_descr.strip()
        descr_lower = descr.lower()
        # Exclude aggregations, VLAN SVIs, loopbacks, user-defined/virtual, etc.
        if any(kw in descr_lower for kw in ("vlan", "loopback", "null", "tunnel", "stack", "trk", "lag", "user defined")):
            return False
        # Only real ethernet interfaces are physical ports on the 1930.
        # ethernetCsmacd(6); reject propVirtual(53) and ieee8023adLag(161).
        if if_type != 6:
            return False
        # Front-panel ports are named as a bare number ("1") or slot form ("1/1").
        if re.fullmatch(r"\d+", descr):
            return True
        if re.match(r"^\d+/\d+", descr):
            return True
        if any(kw in descr_lower for kw in ("gigabit", "fast ethernet", "10g", "ge", "fe")):
            return True
        return False

    def _vlan_bridge_index(
        self,
        ifindex: int,
        port_name: str,
        ifindex_to_bridge: dict[int, int],
    ) -> int:
        """Map an interface to the Q-BRIDGE PortList / PVID index.

        Instant On PortLists are keyed by bridge port, which matches ifIndex
        (copper 1–24, fibre often 49–52). Do **not** use the front-panel label
        when it diverges (e.g. panel ``25`` → ifIndex ``49``); that drops tagged
        VLAN membership on SFP uplinks.
        """
        if ifindex in ifindex_to_bridge:
            return ifindex_to_bridge[ifindex]
        # No bridge-port row: fall back to ifIndex, then panel number.
        if ifindex:
            return ifindex
        if re.fullmatch(r"\d+", port_name.strip()):
            return int(port_name.strip())
        return ifindex

    @staticmethod
    def _extract_model(sys_descr: str) -> str:
        match = re.search(r"(JL\d+[A-Z])", sys_descr, re.I)
        if match:
            return match.group(1).upper()
        match = re.search(r"(1930\s*\S*)", sys_descr, re.I)
        if match:
            return match.group(1).strip()
        return "1930 Series"
