"""SNMP client supporting v1/v2c communities and SNMPv3 USM (read-only)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, Union

from pysnmp.hlapi.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    UsmUserData,
    getCmd,
    walkCmd,
)

from swi_mgmt.snmp.v3 import build_usm_user

logger = logging.getLogger(__name__)

AuthData = Union[CommunityData, UsmUserData]


class SnmpError(Exception):
    """Raised when an SNMP operation fails."""


class SnmpClient:
    """Async SNMP client for v1/v2c and SNMPv3 read-only access."""

    def __init__(
        self,
        host: str,
        community: str = "public",
        version: int = 2,
        port: int = 161,
        timeout: float = 3.0,
        retries: int = 1,
        engine: SnmpEngine | None = None,
        *,
        v3_user: str = "",
        v3_auth_proto: str = "sha",
        v3_auth_key: str = "",
        v3_priv_proto: str = "aes128",
        v3_priv_key: str = "",
    ) -> None:
        self.host = host
        self.community = community
        self.version = int(version)
        self.port = port
        self.timeout = timeout
        self.retries = retries
        self.v3_user = v3_user
        self.v3_auth_proto = v3_auth_proto
        self.v3_auth_key = v3_auth_key
        self.v3_priv_proto = v3_priv_proto
        self.v3_priv_key = v3_priv_key
        self._owns_engine = engine is None
        self._engine = engine if engine is not None else SnmpEngine()
        self._auth: AuthData | None = None

    def _auth_data(self) -> AuthData:
        if self._auth is not None:
            return self._auth
        if self.version == 3:
            try:
                self._auth = build_usm_user(
                    self.v3_user,
                    self.v3_auth_proto,
                    self.v3_auth_key,
                    self.v3_priv_proto,
                    self.v3_priv_key,
                )
            except ValueError as exc:
                raise SnmpError(str(exc)) from exc
        else:
            mp_model = 0 if self.version == 1 else 1
            self._auth = CommunityData(self.community, mpModel=mp_model)
        return self._auth

    def _transport(self) -> UdpTransportTarget:
        return UdpTransportTarget(
            (self.host, self.port),
            timeout=self.timeout,
            retries=self.retries,
        )

    async def get(self, oid: str) -> Any:
        """GET a single OID value."""
        error_indication, error_status, _error_index, var_binds = await getCmd(
            self._engine,
            self._auth_data(),
            self._transport(),
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
        )
        if error_indication:
            raise SnmpError(str(error_indication))
        if error_status:
            raise SnmpError(str(error_status))
        _oid, value = var_binds[0]
        return self._unwrap(value)

    async def get_many(self, oids: list[str]) -> dict[str, Any]:
        """GET multiple OIDs in one request."""
        object_types = [ObjectType(ObjectIdentity(oid)) for oid in oids]
        error_indication, error_status, _error_index, var_binds = await getCmd(
            self._engine,
            self._auth_data(),
            self._transport(),
            ContextData(),
            *object_types,
        )
        if error_indication:
            raise SnmpError(str(error_indication))
        if error_status:
            raise SnmpError(str(error_status))
        result: dict[str, Any] = {}
        for oid, var_bind in zip(oids, var_binds):
            result[oid] = self._unwrap(var_bind[1])
        return result

    async def walk(self, base_oid: str) -> dict[str, Any]:
        """WALK a subtree. Uses GetBulk automatically for SNMPv2c/v3."""
        if self.version != 1:
            return await self.bulk_walk(base_oid)

        results: dict[str, Any] = {}
        base_prefix = base_oid.rstrip(".") + "."

        async for error_indication, error_status, _error_index, var_binds in walkCmd(
            self._engine,
            self._auth_data(),
            self._transport(),
            ContextData(),
            ObjectType(ObjectIdentity(base_oid)),
            lexicographicMode=False,
        ):
            if error_indication:
                raise SnmpError(str(error_indication))
            if error_status:
                break

            oid_str, value = var_binds[0]
            oid_str = str(oid_str)
            if not oid_str.startswith(base_prefix) and oid_str != base_oid:
                break

            results[oid_str] = self._unwrap(value)

        return results

    async def bulk_walk(self, base_oid: str, max_repetitions: int = 40) -> dict[str, Any]:
        """Bulk walk for faster subtree retrieval (v2c/v3; falls back for v1)."""
        if self.version == 1:
            results: dict[str, Any] = {}
            base_prefix = base_oid.rstrip(".") + "."
            async for error_indication, error_status, _error_index, var_binds in walkCmd(
                self._engine,
                self._auth_data(),
                self._transport(),
                ContextData(),
                ObjectType(ObjectIdentity(base_oid)),
                lexicographicMode=False,
            ):
                if error_indication:
                    raise SnmpError(str(error_indication))
                if error_status:
                    break
                oid_str, value = var_binds[0]
                oid_str = str(oid_str)
                if not oid_str.startswith(base_prefix) and oid_str != base_oid:
                    break
                results[oid_str] = self._unwrap(value)
            return results

        from pysnmp.hlapi.asyncio import bulkWalkCmd

        results: dict[str, Any] = {}
        base_prefix = base_oid.rstrip(".") + "."

        async for error_indication, error_status, _error_index, var_binds in bulkWalkCmd(
            self._engine,
            self._auth_data(),
            self._transport(),
            ContextData(),
            0,
            max_repetitions,
            ObjectType(ObjectIdentity(base_oid)),
            maxRows=10000,
            maxCalls=500,
        ):
            if error_indication:
                raise SnmpError(str(error_indication))
            if error_status:
                break

            for var_bind in var_binds:
                oid_str = str(var_bind[0])
                if not oid_str.startswith(base_prefix) and oid_str != base_oid:
                    return results
                results[oid_str] = self._unwrap(var_bind[1])

        return results

    async def probe(self) -> bool:
        """Check if the host responds to SNMP."""
        try:
            await self.get("1.3.6.1.2.1.1.1.0")
            return True
        except (SnmpError, asyncio.TimeoutError, OSError):
            return False

    @staticmethod
    def _unwrap(value: Any) -> Any:
        """Convert pysnmp types to Python natives.

        OctetString values are returned as ``str`` when they hold printable
        text (e.g. sysName, ifDescr, VLAN names) and as raw ``bytes`` when they
        hold binary data such as Q-BRIDGE-MIB PortList bitmaps. This avoids the
        common pitfall where ``prettyPrint()`` renders a binary OctetString as a
        ``"0x...."`` hex string, which would then be misparsed as bitmap bytes.
        """
        try:
            from pysnmp.proto.rfc1902 import OctetString

            if isinstance(value, OctetString):
                raw = value.asOctets()
                if not raw:
                    return ""
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    return raw
                if any(ord(c) < 32 and c not in "\t\n\r" for c in text):
                    return raw
                return text

            return value.prettyPrint() if hasattr(value, "prettyPrint") else value
        except Exception:
            try:
                return value.prettyPrint() if hasattr(value, "prettyPrint") else value
            except Exception:
                return value

    def close(self) -> None:
        if not self._owns_engine:
            return
        try:
            self._engine.closeDispatcher()
        except (AttributeError, RuntimeError):
            pass

    async def __aenter__(self) -> SnmpClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        self.close()


def close_engine(engine: SnmpEngine) -> None:
    """Safely shut down a shared SNMP engine."""
    try:
        engine.closeDispatcher()
    except (AttributeError, RuntimeError):
        pass


async def probe_host(
    host: str,
    community: str = "public",
    version: int = 2,
    timeout: float = 2.0,
    engine: SnmpEngine | None = None,
    retries: int = 1,
) -> Optional[dict[str, str]]:
    """Probe a host for SNMP; return sys info if reachable (v1/v2c)."""
    client = SnmpClient(
        host, community, version, timeout=timeout, retries=retries, engine=engine
    )
    try:
        info = await client.get_many(
            [
                "1.3.6.1.2.1.1.1.0",
                "1.3.6.1.2.1.1.5.0",
                "1.3.6.1.2.1.1.2.0",
            ]
        )
        return {
            "host": host,
            "sys_descr": str(info.get("1.3.6.1.2.1.1.1.0", "")),
            "sys_name": str(info.get("1.3.6.1.2.1.1.5.0", "")),
            "sys_object_id": str(info.get("1.3.6.1.2.1.1.2.0", "")),
        }
    except (SnmpError, asyncio.TimeoutError, OSError):
        return None
    finally:
        client.close()
