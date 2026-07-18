"""Application state for the HTTP API."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Literal, Optional

from pysnmp.hlapi.asyncio import SnmpEngine

from swi_mgmt.config import AppConfig, SwitchConfig, load_config, save_config
from swi_mgmt.drivers.registry import detect_driver, list_drivers
from swi_mgmt.models.switch import SwitchSnapshot
from swi_mgmt.services.switch_service import fetch_snapshot, run_scan
from swi_mgmt.session.vlan_registry import (
    SessionVlanRegistry,
    VlanConflict,
    aggregate_port_counts,
)
from swi_mgmt.snmp.scanner import ScanResult, suggest_scan_cidr

logger = logging.getLogger(__name__)

SnapshotMode = Literal["full", "live", "fast"]


@dataclass
class ScanState:
    running: bool = False
    phase: str = ""  # "ping" | "snmp" | ""
    ping_done: int = 0
    ping_total: int = 0
    snmp_done: int = 0
    snmp_total: int = 0  # ping survivors / SNMP candidates
    results: list[ScanResult] = field(default_factory=list)
    error: str = ""
    _task: Optional[asyncio.Task] = None
    _cancel: Optional[asyncio.Event] = None


class AppState:
    def __init__(self) -> None:
        self.config = load_config()
        self.snapshots: dict[str, SwitchSnapshot] = {}
        self.prev_counters: dict[str, dict] = {}
        self.session_vlans = SessionVlanRegistry()
        self.highlight_vlan: int | None = None
        self.pending_conflicts: list[VlanConflict] = []
        self.scan = ScanState()
        self._conflict_resolutions: dict[int, str] = {}
        self._snmp_engine = SnmpEngine()
        self._refresh_locks: dict[str, asyncio.Lock] = {}
        self._structure_fetched_at: dict[str, float] = {}
        self._prefetch_task: Optional[asyncio.Task] = None
        self._prefetch_sem = asyncio.Semaphore(max(1, self.config.prefetch_concurrency))
        # host -> (monotonic_ts, ptr_or_None); negative results cached too
        self._ptr_cache: dict[str, tuple[float, str | None]] = {}

    def save(self) -> None:
        save_config(self.config)

    def get_switch(self, host: str) -> SwitchConfig | None:
        for sw in self.config.switches:
            if sw.host == host:
                return sw
        return None

    async def resolve_ptr(self, host: str, *, timeout: float = 0.75) -> str | None:
        """Cached reverse-DNS lookup for a switch host."""
        from swi_mgmt.dnsutil import reverse_dns_lookup

        key = (host or "").strip()
        if not key:
            return None
        now = time.monotonic()
        cached = self._ptr_cache.get(key)
        if cached and now - cached[0] < 300.0:
            return cached[1]
        try:
            name = await asyncio.wait_for(
                asyncio.to_thread(reverse_dns_lookup, key),
                timeout=timeout,
            )
        except (asyncio.TimeoutError, OSError):
            name = None
        self._ptr_cache[key] = (now, name)
        return name

    async def resolve_ptrs(self, hosts: list[str]) -> dict[str, str | None]:
        """Resolve PTR records for many hosts in parallel."""
        unique = list(dict.fromkeys(h.strip() for h in hosts if h and h.strip()))
        if not unique:
            return {}
        results = await asyncio.gather(*(self.resolve_ptr(h) for h in unique))
        return dict(zip(unique, results, strict=True))

    def rename_switch_state(self, old_host: str, new_host: str) -> None:
        """Migrate cached snapshot/counters when a switch host is renamed."""
        if old_host == new_host:
            return
        if old_host in self.snapshots:
            self.snapshots[new_host] = self.snapshots.pop(old_host)
            snap = self.snapshots[new_host]
            snap.identity.host = new_host
        if old_host in self.prev_counters:
            self.prev_counters[new_host] = self.prev_counters.pop(old_host)
        if old_host in self._structure_fetched_at:
            self._structure_fetched_at[new_host] = self._structure_fetched_at.pop(old_host)
        if old_host in self._refresh_locks:
            self._refresh_locks[new_host] = self._refresh_locks.pop(old_host)
        if old_host in self._ptr_cache:
            self._ptr_cache[new_host] = self._ptr_cache.pop(old_host)

    def invalidate_switch_cache(self, host: str) -> None:
        """Drop cached snapshot so the next load re-queries the device."""
        self.snapshots.pop(host, None)
        self.prev_counters.pop(host, None)
        self._structure_fetched_at.pop(host, None)

    def prune_switch_caches(self, keep_hosts: set[str]) -> None:
        """Drop cached state for hosts no longer in the inventory."""
        for host in list(self.snapshots):
            if host not in keep_hosts:
                self.invalidate_switch_cache(host)
        for host in list(self._ptr_cache):
            if host not in keep_hosts:
                self._ptr_cache.pop(host, None)
        for host in list(self._refresh_locks):
            if host not in keep_hosts:
                self._refresh_locks.pop(host, None)

    def apply_scenario(self, data: object, *, mode: str = "replace") -> dict:
        """Import a scenario document into the live config and persist it."""
        from swi_mgmt.scenario import apply_scenario, parse_scenario

        imported_switches, _settings, _name = parse_scenario(data)
        imported_hosts = {s.host for s in imported_switches}
        _config, summary = apply_scenario(self.config, data, mode=mode)  # type: ignore[arg-type]

        keep = {s.host for s in self.config.switches}
        if mode == "replace":
            self.prune_switch_caches(keep)
        for host in imported_hosts:
            self.invalidate_switch_cache(host)

        self._prefetch_sem = asyncio.Semaphore(max(1, self.config.prefetch_concurrency))
        self.save()
        return summary

    def _conflict_resolver(self, conflict: VlanConflict) -> str:
        if conflict.vlan_id in self._conflict_resolutions:
            return self._conflict_resolutions[conflict.vlan_id]
        if conflict not in self.pending_conflicts:
            self.pending_conflicts.append(conflict)
        return conflict.session_name

    def merge_snapshot(
        self,
        snapshot: SwitchSnapshot,
        *,
        update_counters: bool = True,
    ) -> SwitchSnapshot:
        merged = self.session_vlans.merge_snapshot(snapshot, self._conflict_resolver)
        self.snapshots[snapshot.identity.host] = merged
        # Fast mode skips octet walks; do not overwrite real counter baselines with zeros
        # (that makes the next live rate sample wildly wrong).
        if update_counters:
            self.prev_counters[snapshot.identity.host] = {
                p.index: (p.in_octets, p.out_octets, merged.timestamp)
                for p in merged.ports
            }
        return merged

    def _resolve_mode(self, mode: SnapshotMode | None) -> SnapshotMode:
        if mode:
            return mode
        return "fast" if self.config.snmp_fast_mode else "full"

    def _structure_is_fresh(self, host: str) -> bool:
        fetched = self._structure_fetched_at.get(host)
        if fetched is None or host not in self.snapshots:
            return False
        return (time.monotonic() - fetched) < self.config.structure_cache_sec

    async def refresh_switch(
        self,
        host: str,
        mode: SnapshotMode | None = None,
    ) -> SwitchSnapshot:
        cfg = self.get_switch(host)
        if not cfg:
            raise KeyError(f"Switch not found: {host}")
        lock = self._refresh_locks.setdefault(host, asyncio.Lock())
        async with lock:
            resolved = self._resolve_mode(mode)
            prior = self.snapshots.get(host)

            # Live polls reuse cached VLAN/structure when still fresh.
            if resolved == "live" and prior and self._structure_is_fresh(host):
                fetch_mode: SnapshotMode = "live"
            elif resolved == "live":
                fetch_mode = "fast" if self.config.snmp_fast_mode else "full"
            else:
                fetch_mode = resolved

            prev = self.prev_counters.get(host)
            raw = await fetch_snapshot(
                cfg,
                prev,
                timeout=self.config.snmp_timeout,
                retries=self.config.snmp_retries,
                engine=self._snmp_engine,
                mode=fetch_mode,
                prior=prior if fetch_mode == "live" else None,
            )
            if fetch_mode in ("full", "fast"):
                self._structure_fetched_at[host] = time.monotonic()
            return self.merge_snapshot(raw, update_counters=fetch_mode != "fast")

    def schedule_prefetch(self, exclude_host: str) -> None:
        """Stagger background refresh of other configured switches."""
        if self._prefetch_task and not self._prefetch_task.done():
            return
        neighbors = [s.host for s in self.config.switches if s.host != exclude_host]
        if not neighbors:
            return

        async def _run() -> None:
            for neighbor in neighbors:
                if neighbor in self.snapshots and self._structure_is_fresh(neighbor):
                    continue
                async with self._prefetch_sem:
                    try:
                        mode: SnapshotMode = "fast" if self.config.snmp_fast_mode else "full"
                        await self.refresh_switch(neighbor, mode=mode)
                    except Exception as exc:
                        logger.debug("Prefetch failed for %s: %s", neighbor, exc)
                await asyncio.sleep(0.15)

        self._prefetch_task = asyncio.create_task(_run())

    def resolve_conflict(self, vlan_id: int, choice: str) -> None:
        conflict = next((c for c in self.pending_conflicts if c.vlan_id == vlan_id), None)
        if not conflict:
            return
        if choice == "switch":
            self._conflict_resolutions[vlan_id] = conflict.switch_name
            self.session_vlans.set_vlan_name(vlan_id, conflict.switch_name)
        else:
            self._conflict_resolutions[vlan_id] = conflict.session_name
        self.pending_conflicts = [c for c in self.pending_conflicts if c.vlan_id != vlan_id]
        for host, snap in list(self.snapshots.items()):
            self.merge_snapshot(snap)

    def resolve_all_conflicts(self, choice: str) -> None:
        pending = list(self.pending_conflicts)
        for conflict in pending:
            self.resolve_conflict(conflict.vlan_id, choice)

    def session_state(self) -> dict:
        counts = aggregate_port_counts(self.snapshots)
        vlans = []
        for vlan in self.session_vlans.all_vlans():
            c = counts.get(vlan.vlan_id)
            vlans.append({
                "vlan_id": vlan.vlan_id,
                "name": vlan.name,
                "color": self.session_vlans.get_color(vlan.vlan_id),
                "port_count": c.total if c else 0,
                "untagged_count": c.untagged if c else 0,
                "tagged_count": c.tagged if c else 0,
            })
        return {
            "vlans": vlans,
            "highlight_vlan": self.highlight_vlan,
            "pending_conflicts": [
                {
                    "vlan_id": c.vlan_id,
                    "session_name": c.session_name,
                    "switch_name": c.switch_name,
                    "switch_host": c.switch_host,
                    "switch_label": c.switch_label,
                }
                for c in self.pending_conflicts
            ],
        }

    async def start_scan(
        self,
        cidr: str = "",
        community: str = "",
        version: int = 0,
    ) -> None:
        if self.scan.running:
            return
        switch_hosts = [s.host for s in self.config.switches]
        suggested = suggest_scan_cidr(switch_hosts)
        saved = (self.config.scan_subnet or "").strip()

        def _saved_matches_inventory(saved_cidr: str) -> bool:
            if not saved_cidr or not switch_hosts:
                return False
            try:
                net = __import__("ipaddress").ip_network(saved_cidr, strict=False)
                return any(
                    __import__("ipaddress").ip_address(h) in net for h in switch_hosts
                )
            except ValueError:
                return False

        if not cidr:
            if _saved_matches_inventory(saved):
                cidr = saved
            elif switch_hosts:
                cidr = suggested
            else:
                cidr = saved or suggested
        community = community or self.config.scan_community
        version = version or self.config.scan_version

        self.scan = ScanState(running=True, phase="ping")
        self.scan._cancel = asyncio.Event()

        async def progress(phase: str, done: int, total: int) -> None:
            self.scan.phase = phase or self.scan.phase
            if phase == "ping":
                self.scan.ping_done = done
                self.scan.ping_total = total
            elif phase == "snmp":
                self.scan.snmp_done = done
                self.scan.snmp_total = total

        extra_communities = [s.community for s in self.config.switches]

        async def do_scan() -> None:
            try:
                # Dedicated short timeout for discovery (not the full poll timeout).
                scan_timeout = min(2.0, max(1.0, float(self.config.snmp_timeout or 2.0)))
                results = await run_scan(
                    cidr,
                    community,
                    version,
                    timeout=scan_timeout,
                    progress_callback=progress,
                    cancel_event=self.scan._cancel,
                    communities=extra_communities,
                    include_icmp_only=True,
                )
                self.scan.results = results
            except Exception as exc:
                self.scan.error = str(exc)
                logger.exception("Scan failed")
            finally:
                self.scan.running = False
                self.scan.phase = ""

        self.scan._task = asyncio.create_task(do_scan())

    def cancel_scan(self) -> None:
        if self.scan._cancel:
            self.scan._cancel.set()

    def scan_status(self) -> dict:
        results = []
        for r in self.scan.results:
            if r.snmp_ok:
                driver_cls = detect_driver(r.sys_descr, r.sys_object_id)
                driver_id = driver_cls.driver_id
                driver_name = driver_cls.display_name
            else:
                driver_id = ""
                driver_name = "No SNMP"
            results.append({
                "host": r.host,
                "sys_name": r.sys_name,
                "sys_descr": r.sys_descr,
                "driver_id": driver_id,
                "driver_name": driver_name,
                "snmp_ok": r.snmp_ok,
            })
        return {
            "running": self.scan.running,
            "phase": self.scan.phase,
            "ping_done": self.scan.ping_done,
            "ping_total": self.scan.ping_total,
            "snmp_done": self.scan.snmp_done,
            "snmp_total": self.scan.snmp_total,
            "error": self.scan.error,
            "results": results,
        }

    def drivers(self) -> list[dict]:
        return list_drivers()
