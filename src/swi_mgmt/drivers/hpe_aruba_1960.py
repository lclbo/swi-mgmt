"""HPE / Aruba Instant On 1960 series switch driver."""

from __future__ import annotations

import re

from swi_mgmt.drivers.hpe_aruba_1930 import HpeAruba1930Driver
from swi_mgmt.models.switch import SwitchIdentity


class HpeAruba1960Driver(HpeAruba1930Driver):
    """Driver for HPE Networking Instant On 1960 series stackable switches.

    Same Q-BRIDGE / numeric ifDescr quirks as the 1930 family, plus extra
    filtering for stacking virtual interfaces that still appear when standalone.
    """

    driver_id = "hpe_aruba_1960"
    display_name = "HPE / Aruba Instant On 1960"
    description = (
        "Instant On 1960 series (JL805A aggregation, JL806A/JL807A 24G, "
        "JL808A/JL809A 48G, and related SKUs)"
    )

    _MATCH_PATTERNS = [
        re.compile(r"aruba.*1960", re.I),
        re.compile(r"instant\s*on.*1960", re.I),
        re.compile(r"JL80[5-9]A", re.I),
        re.compile(r"JL81[0-9]A", re.I),
        re.compile(r"HP.*1960", re.I),
        re.compile(r"HPE.*1960", re.I),
    ]

    # Extra noise seen on 1960 (stack member ghosts, OOB, etc.)
    _EXCLUDE_EXTRA = (
        "member",
        "oobm",
        "out-of-band",
        "stacking",
        "backplane",
        "internal",
    )

    @classmethod
    def matches(cls, sys_descr: str, sys_object_id: str = "") -> bool:
        blob = f"{sys_descr} {sys_object_id}"
        for pattern in cls._MATCH_PATTERNS:
            if pattern.search(blob):
                return True
        return False

    async def get_identity(self) -> SwitchIdentity:
        identity = await super().get_identity()
        identity.vendor = "HPE / Aruba"
        identity.model = self._extract_model(identity.sys_descr)
        identity.driver_id = self.driver_id
        return identity

    def is_physical_port(self, if_type: int, if_descr: str) -> bool:
        descr_lower = if_descr.strip().lower()
        if any(kw in descr_lower for kw in self._EXCLUDE_EXTRA):
            return False
        return super().is_physical_port(if_type, if_descr)

    @staticmethod
    def _extract_model(sys_descr: str) -> str:
        match = re.search(r"(JL\d+[A-Z])", sys_descr, re.I)
        if match:
            return match.group(1).upper()
        match = re.search(r"(1960\s*\S*)", sys_descr, re.I)
        if match:
            return match.group(1).strip()
        return "1960 Series"
