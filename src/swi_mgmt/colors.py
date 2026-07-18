"""Deterministic VLAN color mapping (framework-agnostic)."""

from __future__ import annotations

# Fixed accent for ports carrying multiple VLANs (trunk / tagged).
MULTI_VLAN_COLOR = "#f59e0b"

DIMMED_ROW_BG = "#12121c"
DIMMED_FG = "#4b5563"
NORMAL_ROW_BG = "#1e1e32"
NORMAL_ROW_ALT_BG = "#252540"
HIGHLIGHT_DIM_OPACITY = 0.28


def _hsv_to_hex(hue: int, sat: int = 170, val: int = 210) -> str:
    """Convert HSV (0-360, 0-255, 0-255) to #rrggbb."""
    import colorsys

    r, g, b = colorsys.hsv_to_rgb(hue / 360.0, sat / 255.0, val / 255.0)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def assign_vlan_color(vlan_id: int, pool: dict[int, str]) -> str:
    """Assign and store a stable session color for a VLAN ID."""
    if vlan_id in pool:
        return pool[vlan_id]
    if vlan_id <= 0:
        color = "#64748b"
    else:
        hue = (vlan_id * 53) % 360
        color = _hsv_to_hex(hue)
    pool[vlan_id] = color
    return color


def vlan_color_hex(vlan_id: int, pool: dict[int, str] | None = None) -> str:
    if pool is not None:
        return assign_vlan_color(vlan_id, pool)
    if vlan_id <= 0:
        return "#64748b"
    return _hsv_to_hex((vlan_id * 53) % 360)


def text_on_background(hex_color: str) -> str:
    c = hex_color.lstrip("#")
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return "#0f172a" if luminance > 150 else "#f8fafc"


def port_vlan_ids(port) -> set[int]:
    vlans = {port.primary_vlan}
    vlans.update(port.tagged_vlans)
    return vlans


def port_has_multiple_vlans(port) -> bool:
    return len(port_vlan_ids(port)) > 1


def port_has_vlan(port, vlan_id: int) -> bool:
    return vlan_id in port_vlan_ids(port)
