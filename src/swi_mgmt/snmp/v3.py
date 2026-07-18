"""SNMPv3 USM helpers (read-only)."""

from __future__ import annotations

from typing import Any

from pysnmp.hlapi.asyncio import (
    UsmUserData,
    usmAesCfb128Protocol,
    usmAesCfb192Protocol,
    usmAesCfb256Protocol,
    usmDESPrivProtocol,
    usmHMAC128SHA224AuthProtocol,
    usmHMAC192SHA256AuthProtocol,
    usmHMAC256SHA384AuthProtocol,
    usmHMAC384SHA512AuthProtocol,
    usmHMACMD5AuthProtocol,
    usmHMACSHAAuthProtocol,
    usmNoAuthProtocol,
    usmNoPrivProtocol,
)

# Normalized protocol ids stored in config / shown in UI.
AUTH_PROTOCOLS = ("none", "md5", "sha", "sha224", "sha256", "sha384", "sha512")
PRIV_PROTOCOLS = ("none", "des", "aes128", "aes192", "aes256")

_AUTH_MAP = {
    "none": usmNoAuthProtocol,
    "md5": usmHMACMD5AuthProtocol,
    "sha": usmHMACSHAAuthProtocol,
    "sha224": usmHMAC128SHA224AuthProtocol,
    "sha256": usmHMAC192SHA256AuthProtocol,
    "sha384": usmHMAC256SHA384AuthProtocol,
    "sha512": usmHMAC384SHA512AuthProtocol,
}

_PRIV_MAP = {
    "none": usmNoPrivProtocol,
    "des": usmDESPrivProtocol,
    "aes": usmAesCfb128Protocol,
    "aes128": usmAesCfb128Protocol,
    "aes192": usmAesCfb192Protocol,
    "aes256": usmAesCfb256Protocol,
}


def normalize_auth_proto(value: str | None) -> str:
    key = (value or "sha").strip().lower()
    return key if key in _AUTH_MAP else "sha"


def normalize_priv_proto(value: str | None) -> str:
    key = (value or "aes128").strip().lower()
    if key == "aes":
        return "aes128"
    return key if key in _PRIV_MAP else "aes128"


def build_usm_user(
    user: str,
    auth_proto: str = "sha",
    auth_key: str = "",
    priv_proto: str = "aes128",
    priv_key: str = "",
) -> UsmUserData:
    """Build UsmUserData from normalized protocol names and keys."""
    user_name = (user or "").strip()
    if not user_name:
        raise ValueError("SNMPv3 user is required")

    auth = normalize_auth_proto(auth_proto)
    priv = normalize_priv_proto(priv_proto)
    auth_protocol = _AUTH_MAP[auth]
    priv_protocol = _PRIV_MAP[priv]

    kwargs: dict[str, Any] = {"userName": user_name}

    if auth != "none":
        if not (auth_key or "").strip():
            raise ValueError("SNMPv3 auth key is required when auth protocol is set")
        kwargs["authKey"] = auth_key
        kwargs["authProtocol"] = auth_protocol
    else:
        kwargs["authProtocol"] = usmNoAuthProtocol
        # Privacy requires authentication in USM.
        priv = "none"
        priv_protocol = usmNoPrivProtocol

    if priv != "none":
        if not (priv_key or "").strip():
            raise ValueError("SNMPv3 privacy key is required when priv protocol is set")
        kwargs["privKey"] = priv_key
        kwargs["privProtocol"] = priv_protocol
    else:
        kwargs["privProtocol"] = usmNoPrivProtocol

    return UsmUserData(**kwargs)
