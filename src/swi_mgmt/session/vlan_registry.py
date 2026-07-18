"""Session-wide VLAN name registry shared across all switches."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

from swi_mgmt.colors import assign_vlan_color
from swi_mgmt.models.switch import SwitchSnapshot, VlanInfo


@dataclass(frozen=True)
class VlanConflict:
    vlan_id: int
    session_name: str
    switch_name: str
    switch_host: str
    switch_label: str


ConflictResolver = Callable[[VlanConflict], str]


class SessionVlanRegistry:
    """Maps VLAN IDs to display names and colors for the current session."""

    def __init__(self) -> None:
        self._names: dict[int, str] = {}
        self._colors: dict[int, str] = {}

    def get_name(self, vlan_id: int) -> str:
        return self._names.get(vlan_id, "")

    def get_color(self, vlan_id: int) -> str:
        return assign_vlan_color(vlan_id, self._colors)

    def color_pool(self) -> dict[int, str]:
        return dict(self._colors)

    def all_vlans(self) -> list[VlanInfo]:
        return [
            VlanInfo(vlan_id=vid, name=name)
            for vid, name in sorted(self._names.items())
        ]

    def merge_snapshot(
        self,
        snapshot: SwitchSnapshot,
        resolve_conflict: ConflictResolver,
    ) -> SwitchSnapshot:
        switch_label = snapshot.identity.sys_name or snapshot.identity.host

        for vlan in snapshot.vlans:
            self._merge_vlan(
                vlan.vlan_id,
                vlan.name.strip(),
                snapshot.identity.host,
                switch_label,
                resolve_conflict,
            )

        for port in snapshot.ports:
            self._ensure_vlan_id(port.primary_vlan)
            for vid in port.untagged_vlans:
                self._ensure_vlan_id(vid)
            for vid in port.tagged_vlans:
                self._ensure_vlan_id(vid)

        return self.apply_to_snapshot(snapshot)

    def _ensure_vlan_id(self, vlan_id: int) -> None:
        if vlan_id not in self._names:
            self._names[vlan_id] = ""
        assign_vlan_color(vlan_id, self._colors)

    def _merge_vlan(
        self,
        vlan_id: int,
        incoming_name: str,
        switch_host: str,
        switch_label: str,
        resolve_conflict: ConflictResolver,
    ) -> None:
        existing = self._names.get(vlan_id, "").strip()

        if vlan_id not in self._names:
            self._names[vlan_id] = incoming_name
            assign_vlan_color(vlan_id, self._colors)
            return

        if not incoming_name or not existing:
            if incoming_name and not existing:
                self._names[vlan_id] = incoming_name
            return

        if incoming_name.lower() == existing.lower():
            return

        chosen = resolve_conflict(
            VlanConflict(
                vlan_id=vlan_id,
                session_name=existing,
                switch_name=incoming_name,
                switch_host=switch_host,
                switch_label=switch_label,
            )
        )
        self._names[vlan_id] = chosen.strip()

    def apply_to_snapshot(self, snapshot: SwitchSnapshot) -> SwitchSnapshot:
        vlans = []
        seen: set[int] = set()
        for vlan in snapshot.vlans:
            seen.add(vlan.vlan_id)
            vlans.append(replace(vlan, name=self.get_name(vlan.vlan_id) or vlan.name))
        for vid in sorted(self._names):
            if vid not in seen:
                vlans.append(VlanInfo(vlan_id=vid, name=self._names[vid]))
        vlans.sort(key=lambda v: v.vlan_id)
        return replace(snapshot, vlans=vlans)

    def set_vlan_name(self, vlan_id: int, name: str) -> None:
        self._names[vlan_id] = name.strip()
        assign_vlan_color(vlan_id, self._colors)


@dataclass(frozen=True)
class VlanPortCounts:
    total: int
    untagged: int
    tagged: int


def aggregate_port_counts(snapshots: dict[str, SwitchSnapshot]) -> dict[int, VlanPortCounts]:
    """Count unique ports per VLAN, split into untagged vs tagged membership."""
    untagged_keys: dict[int, set[tuple[str, int]]] = {}
    tagged_keys: dict[int, set[tuple[str, int]]] = {}
    for host, snapshot in snapshots.items():
        for port in snapshot.ports:
            key = (host, port.index)
            untagged = {
                vid
                for vid in (port.untagged_vlans or ([port.primary_vlan] if port.primary_vlan else []))
                if vid
            }
            for vid in untagged:
                untagged_keys.setdefault(vid, set()).add(key)
            for vid in port.tagged_vlans:
                if not vid or vid in untagged:
                    continue
                tagged_keys.setdefault(vid, set()).add(key)
    vlan_ids = set(untagged_keys) | set(tagged_keys)
    return {
        vid: VlanPortCounts(
            total=len(untagged_keys.get(vid, set()) | tagged_keys.get(vid, set())),
            untagged=len(untagged_keys.get(vid, set())),
            tagged=len(tagged_keys.get(vid, set())),
        )
        for vid in vlan_ids
    }
