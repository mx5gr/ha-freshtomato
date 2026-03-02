"""Binary sensor platform for FreshTomato integration.

Binary sensors:
  • WAN Connected            – True when WAN IP is present
  • 2.4 GHz SSID Broadcast   – True when SSID is visible (not hidden)
  • 5 GHz SSID Broadcast     – True when SSID is visible
  • Wireless Client Mode     – True when router has no WAN / acts as AP or repeater
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import FreshTomatoCoordinator, RouterData


@dataclass(frozen=True, kw_only=True)
class FreshTomatoBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Any = None


BINARY_SENSORS: tuple[FreshTomatoBinarySensorDescription, ...] = (
    FreshTomatoBinarySensorDescription(
        key="wan_connected",
        name="WAN Connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        icon="mdi:wan",
        value_fn=lambda d: bool(d.wan_ip and d.wan_ip not in ("0.0.0.0", "")),
    ),
    FreshTomatoBinarySensorDescription(
        key="wl0_broadcast",
        name="2.4 GHz SSID Broadcast",
        device_class=None,
        icon="mdi:broadcast",
        value_fn=lambda d: d.nvram.get("wl0_closed", "0") == "0",
    ),
    FreshTomatoBinarySensorDescription(
        key="wl1_broadcast",
        name="5 GHz SSID Broadcast",
        device_class=None,
        icon="mdi:broadcast",
        value_fn=lambda d: d.nvram.get("wl1_closed", "0") == "0",
    ),
    FreshTomatoBinarySensorDescription(
        key="wireless_client_mode",
        name="Wireless Client Mode",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        icon="mdi:wifi-arrow-left-right",
        value_fn=lambda d: (
            (not d.wan_ip or d.wan_ip in ("", "0.0.0.0"))
            and len(d.dhcp_leases) == 0
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: FreshTomatoCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]

    # Static sensors
    entities: list = [
        FreshTomatoBinarySensor(coordinator, entry, desc)
        for desc in BINARY_SENSORS
    ]

    # Dynamic per-port link sensors — created from etherstates data.
    # Register the listener BEFORE calling async_add_entities so it fires
    # on the first coordinator update even if data was empty at setup time.
    known_ports: set[str] = set()

    def _add_port_entities() -> None:
        if not coordinator.data:
            return
        new: list = []
        for label in coordinator.data.eth_ports:
            if label not in known_ports:
                known_ports.add(label)
                new.append(FreshTomatoPortLinkSensor(coordinator, entry, label))
        if new:
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_listener(_add_port_entities))
    _add_port_entities()  # Try immediately with current data

    async_add_entities(entities)


class FreshTomatoBinarySensor(
    CoordinatorEntity[FreshTomatoCoordinator], BinarySensorEntity
):
    entity_description: FreshTomatoBinarySensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FreshTomatoCoordinator,
        entry: ConfigEntry,
        description: FreshTomatoBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._entry = entry

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=f"FreshTomato ({self._entry.data['host']})",
            manufacturer="FreshTomato Project",
            model=self.coordinator.data.nvram.get("t_model_name", "Router")
            if self.coordinator.data else "Router",
            sw_version=(self.coordinator.data.nvram.get("t_build_time") or self.coordinator.data.nvram.get("os_version"))
            if self.coordinator.data else None,
        )

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        try:
            return self.entity_description.value_fn(self.coordinator.data)
        except Exception:  # pylint: disable=broad-except
            return None


def _port_is_connected(state: str) -> bool:
    """Return True if the port state string indicates an active link."""
    return state not in ("DOWN", "disabled", "")


class FreshTomatoPortLinkSensor(
    CoordinatorEntity[FreshTomatoCoordinator], BinarySensorEntity
):
    """Binary sensor: is a specific Ethernet port connected (link up)?

    One entity per physical port. Created dynamically based on what
    etherstates reports, so works across all router models regardless
    of port count (4-port, 5-port, 8-port, etc.).
    """

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self,
        coordinator: FreshTomatoCoordinator,
        entry: ConfigEntry,
        port_label: str,  # e.g. "WAN", "LAN0", "LAN1" ...
    ) -> None:
        super().__init__(coordinator)
        self._port_label = port_label
        self._entry = entry
        safe_key = port_label.lower().replace(" ", "_")
        self._attr_unique_id = f"{entry.entry_id}_port_{safe_key}_link"
        self._attr_name = f"{port_label} Link"
        self._attr_icon = "mdi:ethernet" if "LAN" in port_label else "mdi:wan"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=f"FreshTomato ({self._entry.data['host']})",
            manufacturer="FreshTomato Project",
            model=self.coordinator.data.nvram.get("t_model_name", "Router")
            if self.coordinator.data else "Router",
        )

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        state = self.coordinator.data.eth_ports.get(self._port_label)
        if state is None:
            return None
        return _port_is_connected(state)

    @property
    def extra_state_attributes(self) -> dict:
        if not self.coordinator.data:
            return {}
        raw = self.coordinator.data.eth_ports.get(self._port_label, "")
        speed, duplex = _decode_port_state(raw)
        return {
            "raw_state": raw,
            "speed_mbps": speed,
            "duplex": duplex,
        }


def _decode_port_state(state: str) -> tuple[int | None, str | None]:
    """Decode a port state string into (speed_mbps, duplex).

    Examples:
        "1000FD" → (1000, "full")
        "100HD"  → (100, "half")
        "DOWN"   → (None, None)
    """
    if not state or state in ("DOWN", "disabled", "ACTIVE"):
        return None, None
    duplex = None
    if state.endswith("FD"):
        duplex = "full"
        speed_str = state[:-2]
    elif state.endswith("HD"):
        duplex = "half"
        speed_str = state[:-2]
    else:
        speed_str = state
    try:
        return int(speed_str), duplex
    except ValueError:
        return None, duplex
