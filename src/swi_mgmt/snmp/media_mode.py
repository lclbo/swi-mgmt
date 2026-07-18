"""Classify Ethernet media as copper vs fiber (MAU + ifDescr hints)."""

from __future__ import annotations

import re
from typing import Optional

# IANA dot3MauType leaf numbers known to be copper (twisted pair / CX).
_COPPER_LEAVES = frozenset(
    {
        1,  # AUI
        2,  # 10Base5
        4,  # 10Base2
        5,  # 10BaseT
        9,  # 10BROAD36
        10,  # 10BaseT FD
        14,  # 100BaseT4
        15,  # 100BaseTX
        17,  # 100BaseT2
        22,  # 1000BaseCX
        29,  # 1000BaseT
        30,  # 1000BaseT HD
        31,  # 1000BaseT FD
        40,  # 10GbaseT
        41,  # 10GbaseCX4
    }
)

# Leaf numbers that are optical / fiber.
_FIBER_LEAVES = frozenset(
    {
        3,  # FOIRL
        8,  # 10BaseFL
        11,  # 10BaseFL (alt registries)
        16,  # 100BaseFX
        19,  # 1000BaseX
        20,  # 1000BaseLX
        21,  # 1000BaseSX
        24,  # 10GbaseX
        25,  # 10GbaseLX4
        26,  # 10GbaseR
        27,  # 10GbaseER
        28,  # 10GbaseLR
        32,  # 10GbaseSR
        33,  # 10GbaseW
        34,  # 10GbaseLW
        35,  # 10GbaseEW
    }
)

_COPPER_NAME_RE = re.compile(
    r"(?:BaseT\b|BaseTX\b|BaseT2\b|BaseT4\b|BaseCX|10GbaseT|10GbaseCX|"
    r"1000BaseT|100BaseT|10BaseT|Twisted|RJ.?45)",
    re.I,
)
_FIBER_NAME_RE = re.compile(
    r"(?:Base[FSXLW]?X\b|BaseSX|BaseLX|BaseFX|BaseFL|BaseSR|BaseLR|BaseER|"
    r"BaseLX4|1000BaseX|10Gbase[RSXLW]|Optical|FOIRL)",
    re.I,
)

# ifDescr side tags used by TP-Link and similar: "gigabit copper 21", "Gigabit Fiber"
_DESCR_FIBER_RE = re.compile(r"\b(?:fiber|fibre|sfp)\b", re.I)
_DESCR_COPPER_RE = re.compile(r"\b(?:copper|rj-?45)\b", re.I)


def media_mode_from_if_descr(if_descr: str) -> Optional[str]:
    """Infer copper/fiber from interface description text."""
    text = (if_descr or "").strip()
    if not text:
        return None
    has_fiber = bool(_DESCR_FIBER_RE.search(text) or _FIBER_NAME_RE.search(text))
    has_copper = bool(_DESCR_COPPER_RE.search(text) or _COPPER_NAME_RE.search(text))
    if has_fiber and not has_copper:
        return "fiber"
    if has_copper and not has_fiber:
        return "copper"
    # Ambiguous (e.g. "combo") → unknown
    return None


def mau_type_to_media_mode(value: object) -> Optional[str]:
    """Map an ifMauType OID/value to ``copper``, ``fiber``, or None if unknown."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in ("0", "1.3.6.1.2.1.26.4.0"):
        return None

    if _COPPER_NAME_RE.search(text) and not _FIBER_NAME_RE.search(text):
        return "copper"
    if _FIBER_NAME_RE.search(text):
        return "fiber"

    m = re.search(r"(?:^|\.)1\.3\.6\.1\.2\.1\.26\.4\.(\d+)\s*$", text)
    if not m:
        m = re.search(r"dot3MauType.*?(\d+)\s*$", text, re.I)
    if m:
        leaf = int(m.group(1))
        if leaf in _FIBER_LEAVES:
            return "fiber"
        if leaf in _COPPER_LEAVES:
            return "copper"
        return None

    if text.isdigit():
        leaf = int(text)
        if leaf in _FIBER_LEAVES:
            return "fiber"
        if leaf in _COPPER_LEAVES:
            return "copper"
    return None


def parse_mau_type_walk(walk: dict[str, object]) -> dict[int, str]:
    """Build ifIndex → media_mode from an ifMauType walk.

    Prefers the first classifiable MAU per ifIndex (usually mauIndex=1).
    """
    by_if: dict[int, str] = {}
    for oid, value in walk.items():
        parts = str(oid).strip(".").split(".")
        if len(parts) < 2:
            continue
        try:
            if_index = int(parts[-2])
        except ValueError:
            continue
        if if_index in by_if:
            continue
        mode = mau_type_to_media_mode(value)
        if mode:
            by_if[if_index] = mode
    return by_if


def resolve_media_mode(
    *,
    oper_up: bool,
    mau_mode: Optional[str] = None,
    descr_mode: Optional[str] = None,
) -> Optional[str]:
    """Active media side only when link is up; otherwise None (keep combo UI)."""
    if not oper_up:
        return None
    return mau_mode or descr_mode
