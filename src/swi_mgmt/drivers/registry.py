"""Driver registry for auto-detecting switch vendor/series."""

from __future__ import annotations

from typing import Type

from swi_mgmt.drivers.base import SwitchDriver
from swi_mgmt.drivers.generic import GenericSnmpDriver
from swi_mgmt.drivers.hpe_aruba_1930 import HpeAruba1930Driver
from swi_mgmt.drivers.hpe_aruba_1960 import HpeAruba1960Driver
from swi_mgmt.drivers.tp_link_sg2424 import TpLinkSg2424Driver
from swi_mgmt.snmp.client import SnmpClient

# Order matters: more specific drivers first, generic last
DRIVERS: list[Type[SwitchDriver]] = [
    HpeAruba1960Driver,
    HpeAruba1930Driver,
    TpLinkSg2424Driver,
    GenericSnmpDriver,
]


def detect_driver(sys_descr: str, sys_object_id: str = "") -> Type[SwitchDriver]:
    """Select the best matching driver for a device."""
    for driver_cls in DRIVERS:
        if driver_cls.matches(sys_descr, sys_object_id):
            return driver_cls
    return GenericSnmpDriver


def create_driver(
    client: SnmpClient,
    sys_descr: str = "",
    sys_object_id: str = "",
    driver_id: str | None = None,
) -> SwitchDriver:
    """Instantiate a driver, optionally forcing a specific driver_id."""
    if driver_id:
        for driver_cls in DRIVERS:
            if driver_cls.driver_id == driver_id:
                return driver_cls(client)
    driver_cls = detect_driver(sys_descr, sys_object_id)
    return driver_cls(client)


def list_drivers() -> list[dict[str, str]]:
    """Return metadata for all registered drivers."""
    return [
        {
            "id": cls.driver_id,
            "name": cls.display_name,
            "description": cls.description,
        }
        for cls in DRIVERS
    ]
