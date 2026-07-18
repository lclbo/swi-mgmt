"""Data models for switch state."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional


class IfOperStatus(IntEnum):
    UP = 1
    DOWN = 2
    TESTING = 3
    UNKNOWN = 4
    DORMANT = 5
    NOT_PRESENT = 6
    LOWER_LAYER_DOWN = 7


class IfAdminStatus(IntEnum):
    UP = 1
    DOWN = 2
    TESTING = 3


@dataclass
class VlanInfo:
    vlan_id: int
    name: str = ""
    status: str = "permanent"


@dataclass
class PortVlanMembership:
    """VLAN membership for a single port."""

    port_index: int
    port_name: str
    primary_vlan: int  # PVID / untagged VLAN
    tagged_vlans: list[int] = field(default_factory=list)
    untagged_vlans: list[int] = field(default_factory=list)


@dataclass
class PoePortStatus:
    """PoE PSE status for a port when POWER-ETHERNET-MIB (or vendor) data exists."""

    # None = unknown; True/False from pethPsePortAdminEnable
    admin_enable: Optional[bool] = None
    # disabled | searching | delivering | fault | test | otherFault
    detection: Optional[str] = None
    # True when actively supplying power (detection == delivering)
    delivering: bool = False
    # Milliwatts when a vendor/extension OID provides it
    power_mw: Optional[int] = None
    # IEEE class 0–4 when reported
    power_class: Optional[int] = None
    # critical | high | low
    priority: Optional[str] = None


@dataclass
class PortStatus:
    index: int
    name: str
    admin_status: IfAdminStatus
    oper_status: IfOperStatus
    speed_mbps: Optional[int] = None
    in_octets: int = 0
    out_octets: int = 0
    in_rate_bps: float = 0.0
    out_rate_bps: float = 0.0
    primary_vlan: int = 1
    untagged_vlans: list[int] = field(default_factory=list)
    tagged_vlans: list[int] = field(default_factory=list)
    # "copper" | "fiber" when known (e.g. combo active side via ifMauType); else None
    media_mode: Optional[str] = None
    # Present only when the port appears in a PoE PSE table
    poe: Optional[PoePortStatus] = None


@dataclass
class SwitchIdentity:
    host: str
    sys_name: str = ""
    sys_descr: str = ""
    vendor: str = ""
    model: str = ""
    driver_id: str = ""


@dataclass
class SwitchSnapshot:
    """Point-in-time view of a switch."""

    identity: SwitchIdentity
    vlans: list[VlanInfo] = field(default_factory=list)
    ports: list[PortStatus] = field(default_factory=list)
    timestamp: float = 0.0
