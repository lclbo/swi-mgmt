"""Parse SNMP PortList octet strings (Q-BRIDGE-MIB)."""


def portlist_to_indices(data: bytes | str) -> list[int]:
    """Convert a PortList octet string to 1-based port indices."""
    if isinstance(data, str):
        data = data.encode("latin-1")
    if not data:
        return []

    indices: list[int] = []
    for byte_idx, byte_val in enumerate(data):
        for bit in range(8):
            if byte_val & (1 << (7 - bit)):
                indices.append(byte_idx * 8 + bit + 1)
    return indices


def indices_to_portlist(indices: list[int], num_ports: int) -> bytes:
    """Convert 1-based port indices to a PortList octet string."""
    num_bytes = (num_ports + 7) // 8
    result = bytearray(num_bytes)
    for idx in indices:
        if idx < 1:
            continue
        zero_based = idx - 1
        byte_idx = zero_based // 8
        bit = 7 - (zero_based % 8)
        if byte_idx < len(result):
            result[byte_idx] |= 1 << bit
    return bytes(result)
