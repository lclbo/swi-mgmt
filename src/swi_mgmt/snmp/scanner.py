"""Network scanner for discovering SNMP-enabled switches."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from pysnmp.hlapi.asyncio import SnmpEngine

from swi_mgmt.snmp.client import close_engine, probe_host

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    host: str
    sys_name: str = ""
    sys_descr: str = ""
    sys_object_id: str = ""
    snmp_ok: bool = True


def _host_to_slash24(ip: str) -> Optional[str]:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if addr.version != 4 or addr.is_loopback or addr.is_link_local:
        return None
    net = ipaddress.ip_network(f"{addr}/24", strict=False)
    return str(net)


def discover_local_ipv4() -> list[str]:
    """Best-effort list of local IPv4 addresses (stdlib only)."""
    found: set[str] = set()

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass

    # Probe several destinations so VPN + LAN routes both surface.
    for dest in (
        "8.8.8.8",
        "1.1.1.1",
        "192.168.0.1",
        "192.168.1.1",
        "10.0.0.1",
        "172.16.0.1",
    ):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.3)
            s.connect((dest, 80))
            found.add(s.getsockname()[0])
            s.close()
        except OSError:
            continue

    return sorted(
        ip
        for ip in found
        if not ip.startswith("127.") and not ip.startswith("169.254.")
    )


def list_candidate_subnets(switch_hosts: Sequence[str] | None = None) -> list[str]:
    """Candidate /24 subnets: inventory first, then local interfaces."""
    ordered: list[str] = []
    seen: set[str] = set()

    def add(cidr: Optional[str]) -> None:
        if cidr and cidr not in seen:
            seen.add(cidr)
            ordered.append(cidr)

    if switch_hosts:
        counts = Counter()
        for host in switch_hosts:
            cidr = _host_to_slash24(host)
            if cidr:
                counts[cidr] += 1
        for cidr, _ in counts.most_common():
            add(cidr)

    local_nets: list[str] = []
    for ip in discover_local_ipv4():
        cidr = _host_to_slash24(ip)
        if cidr:
            local_nets.append(cidr)

    lan_like = [c for c in local_nets if c.startswith(("192.168.", "10.", "172."))]

    def rank(c: str) -> tuple[int, str]:
        # Prefer classic home/office LANs; deprioritize 10.x (often VPN egress).
        if c.startswith("192.168."):
            return (0, c)
        if c.startswith("172."):
            return (1, c)
        return (2, c)

    for cidr in sorted(set(lan_like), key=rank):
        add(cidr)

    for cidr in local_nets:
        add(cidr)

    add(get_local_subnet())
    return ordered


def suggest_scan_cidr(switch_hosts: Sequence[str] | None = None) -> str:
    """Pick the best default scan CIDR for the UI / API."""
    candidates = list_candidate_subnets(switch_hosts)
    if not candidates:
        return "192.168.1.0/24"

    # If inventory exists, always prefer that subnet over VPN egress.
    if switch_hosts:
        counts = Counter(
            c for h in switch_hosts if (c := _host_to_slash24(h)) is not None
        )
        if counts:
            return counts.most_common(1)[0][0]

    # Otherwise prefer 192.168.* among local interfaces.
    for cidr in candidates:
        if cidr.startswith("192.168."):
            return cidr
    return candidates[0]


def get_local_subnet() -> str:
    """Detect a local IPv4 /24 from the primary egress route (best effort)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        cidr = _host_to_slash24(local_ip)
        if cidr:
            return cidr
    except OSError:
        pass
    return "192.168.1.0/24"


def parse_cidr(cidr: str) -> list[str]:
    """Expand a CIDR notation string to a list of host IPs."""
    network = ipaddress.ip_network(cidr, strict=False)
    return [str(h) for h in network.hosts()]


async def icmp_alive(host: str, timeout: float = 0.4) -> bool:
    """Return True if the host answers ICMP echo (best effort)."""
    if sys.platform == "darwin":
        # -W is milliseconds on macOS
        args = ["ping", "-c", "1", "-W", str(max(100, int(timeout * 1000))), host]
    else:
        # -W is seconds on Linux (minimum 1)
        args = ["ping", "-c", "1", "-W", str(max(1, int(round(timeout)))), host]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout + 0.5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return False
        return proc.returncode == 0
    except (OSError, asyncio.CancelledError):
        return False


async def _emit_progress(
    callback: Optional[Callable[..., object]],
    phase: str,
    done: int,
    total: int,
) -> None:
    """Report phase-local progress: (phase, done, total)."""
    if not callback:
        return
    try:
        result = callback(phase, done, total)
    except TypeError:
        # Legacy (completed, total[, phase]) callbacks
        try:
            result = callback(done, total, phase)
        except TypeError:
            result = callback(done, total)
    if asyncio.iscoroutine(result):
        await result


async def ping_sweep(
    hosts: Sequence[str],
    *,
    timeout: float = 0.4,
    concurrency: int = 128,
    cancel_event: Optional[asyncio.Event] = None,
    progress_callback: Optional[Callable[..., object]] = None,
) -> list[str]:
    """Return hosts that answer ICMP, in input order."""
    sem = asyncio.Semaphore(concurrency)
    alive: list[str] = []
    done = 0
    total = len(hosts)

    async def one(host: str) -> Optional[str]:
        nonlocal done
        if cancel_event and cancel_event.is_set():
            return None
        async with sem:
            if cancel_event and cancel_event.is_set():
                return None
            try:
                if await icmp_alive(host, timeout=timeout):
                    return host
                return None
            finally:
                done += 1
                await _emit_progress(progress_callback, "ping", done, total)

    results = await asyncio.gather(*[one(h) for h in hosts], return_exceptions=True)
    for host, item in zip(hosts, results):
        if item == host:
            alive.append(host)
        elif isinstance(item, Exception):
            logger.debug("Ping error %s: %s", host, item)
    return alive


async def scan_subnet(
    cidr: str,
    community: str = "public",
    version: int = 2,
    timeout: float = 1.5,
    concurrency: int = 32,
    progress_callback: Optional[Callable[..., object]] = None,
    cancel_event: Optional[asyncio.Event] = None,
    communities: Sequence[str] | None = None,
    include_icmp_only: bool = True,
    ping_concurrency: int = 128,
    ping_timeout: float = 0.4,
) -> list[ScanResult]:
    """Scan a subnet: fast ICMP sweep, then SNMP only on pingable hosts."""
    hosts = parse_cidr(cidr)
    if not hosts:
        return []

    # Phase 1 — ping sweep (progress is local to this phase).
    await _emit_progress(progress_callback, "ping", 0, len(hosts))

    alive = await ping_sweep(
        hosts,
        timeout=ping_timeout,
        concurrency=ping_concurrency,
        cancel_event=cancel_event,
        progress_callback=progress_callback,
    )

    if cancel_event and cancel_event.is_set():
        return []

    # Phase 2 — SNMP on ping survivors only.
    snmp_total = len(alive)
    await _emit_progress(progress_callback, "snmp", 0, snmp_total)

    if not alive:
        await _emit_progress(progress_callback, "snmp", 0, 0)
        return []

    comm_list: list[str] = []
    for c in [community, *(communities or ())]:
        c = (c or "").strip()
        if c and c not in comm_list:
            comm_list.append(c)
    if "public" not in comm_list:
        comm_list.append("public")
    if not comm_list:
        comm_list = ["public"]

    versions = [version if version in (1, 2) else 2]
    other = 1 if versions[0] == 2 else 2
    versions.append(other)

    results: list[ScanResult] = []
    snmp_done = 0
    semaphore = asyncio.Semaphore(concurrency)
    engine = SnmpEngine()

    async def probe_snmp(host: str) -> Optional[ScanResult]:
        for ver in versions:
            for comm in comm_list:
                if cancel_event and cancel_event.is_set():
                    return None
                info = await probe_host(
                    host,
                    comm,
                    ver,
                    timeout,
                    engine=engine,
                    retries=1,
                )
                if info:
                    return ScanResult(
                        host=info["host"],
                        sys_name=info["sys_name"],
                        sys_descr=info["sys_descr"],
                        sys_object_id=info["sys_object_id"],
                        snmp_ok=True,
                    )
        return None

    async def snmp_one(host: str) -> Optional[ScanResult]:
        nonlocal snmp_done
        if cancel_event and cancel_event.is_set():
            return None
        async with semaphore:
            if cancel_event and cancel_event.is_set():
                return None
            try:
                found = await probe_snmp(host)
                if found:
                    return found
                if include_icmp_only:
                    return ScanResult(
                        host=host,
                        sys_name="",
                        sys_descr="Pingable — no SNMP response",
                        sys_object_id="",
                        snmp_ok=False,
                    )
                return None
            finally:
                snmp_done += 1
                await _emit_progress(
                    progress_callback, "snmp", snmp_done, snmp_total
                )

    try:
        snmp_results = await asyncio.gather(
            *[snmp_one(h) for h in alive], return_exceptions=True
        )
        for item in snmp_results:
            if isinstance(item, ScanResult):
                results.append(item)
            elif isinstance(item, Exception):
                logger.debug("SNMP scan error: %s", item)
    finally:
        close_engine(engine)

    def sort_key(r: ScanResult) -> tuple[int, ipaddress.IPv4Address]:
        try:
            return (0 if r.snmp_ok else 1, ipaddress.ip_address(r.host))  # type: ignore[arg-type]
        except ValueError:
            return (0 if r.snmp_ok else 1, ipaddress.ip_address("0.0.0.0"))

    results.sort(key=sort_key)
    return results
