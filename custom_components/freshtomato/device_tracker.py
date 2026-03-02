"""Device tracker platform for FreshTomato integration.

Tracks both wireless and wired (DHCP) devices.
Each MAC address becomes a tracked device entity in HA.

Wireless devices come from the wldev list (exec=devlist).
Wired devices come from the dhcpd_lease table (also exec=devlist).

This means BOTH wireless and wired tracking require only ONE HTTP call —
a major improvement over the legacy Tomato integration which was wireless-only.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.device_tracker import ScannerEntity, SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_TRACK_WIRED, DATA_COORDINATOR, DEFAULT_TRACK_WIRED, DOMAIN
from .coordinator import FreshTomatoCoordinator

_LOGGER = logging.getLogger(__name__)

SIGNAL_NEW_DEVICE = f"{DOMAIN}_new_device"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: FreshTomatoCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    track_wired: bool = entry.options.get(CONF_TRACK_WIRED, DEFAULT_TRACK_WIRED)

    # Keep track of already-created entities so we don't duplicate
    known_macs: set[str] = set()

    @callback
    def _check_for_new_devices() -> None:
        """Called on each coordinator update; adds entities for new MACs."""
        if coordinator.data is None:
            return

        new_entities: list[FreshTomatoDeviceTracker] = []

        # Wireless devices
        for client in coordinator.data.wireless_clients:
            mac = client["mac"]
            if mac not in known_macs:
                known_macs.add(mac)
                new_entities.append(
                    FreshTomatoDeviceTracker(coordinator, entry, mac, is_wireless=True)
                )

        # Wired / DHCP devices
        if track_wired:
            wireless_macs = {c["mac"] for c in coordinator.data.wireless_clients}
            for lease in coordinator.data.dhcp_leases:
                mac = lease["mac"]
                if mac not in known_macs and mac not in wireless_macs:
                    known_macs.add(mac)
                    new_entities.append(
                        FreshTomatoDeviceTracker(coordinator, entry, mac, is_wireless=False)
                    )

        if new_entities:
            async_add_entities(new_entities)

    # Run once immediately and then on every coordinator update
    _check_for_new_devices()
    entry.async_on_unload(
        coordinator.async_add_listener(_check_for_new_devices)
    )


class FreshTomatoDeviceTracker(
    CoordinatorEntity[FreshTomatoCoordinator], ScannerEntity
):
    """Tracks a single device seen on the FreshTomato router."""

    _attr_source_type = SourceType.ROUTER

    def __init__(
        self,
        coordinator: FreshTomatoCoordinator,
        entry: ConfigEntry,
        mac: str,
        is_wireless: bool,
    ) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._is_wireless = is_wireless
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_tracker_{mac.lower().replace(':', '')}"

    @property
    def is_connected(self) -> bool:
        """Return True if device is currently seen in router tables."""
        if self.coordinator.data is None:
            return False
        if self._is_wireless:
            return any(c["mac"] == self._mac for c in self.coordinator.data.wireless_clients)
        # Wired: check DHCP leases
        return any(l["mac"] == self._mac for l in self.coordinator.data.dhcp_leases)

    @property
    def mac_address(self) -> str:
        return self._mac

    @property
    def hostname(self) -> str | None:
        """Return device hostname from DHCP lease if available."""
        if self.coordinator.data is None:
            return None
        for lease in self.coordinator.data.dhcp_leases:
            if lease["mac"] == self._mac:
                name = lease.get("name", "")
                return name if name and name != "*" else None
        return None

    @property
    def ip_address(self) -> str | None:
        """Return the current IP address of this device."""
        if self.coordinator.data is None:
            return None
        # Check DHCP leases first (most reliable source)
        for lease in self.coordinator.data.dhcp_leases:
            if lease["mac"] == self._mac:
                return lease.get("ip")
        # Fall back to ARP table
        for arp in self.coordinator.data.arp_table:
            if arp["mac"] == self._mac:
                return arp.get("ip")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes like RSSI, interface, connection type."""
        attrs: dict[str, Any] = {
            "connection_type": "wireless" if self._is_wireless else "wired",
        }
        if self.coordinator.data is None:
            return attrs
        if self._is_wireless:
            for client in self.coordinator.data.wireless_clients:
                if client["mac"] == self._mac:
                    attrs.update(
                        {
                            "rssi": client.get("rssi"),
                            "signal_quality": client.get("quality"),
                            "tx_rate_mbps": client.get("tx_rate"),
                            "rx_rate_mbps": client.get("rx_rate"),
                            "interface": client.get("iface"),
                        }
                    )
                    break
        else:
            for lease in self.coordinator.data.dhcp_leases:
                if lease["mac"] == self._mac:
                    attrs["lease_remaining_sec"] = lease.get("lease")
                    break
        return attrs

    @property
    def name(self) -> str:
        """Use hostname from DHCP if available, otherwise format the MAC."""
        hostname = self.hostname
        if hostname:
            return hostname
        return self._mac

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=f"FreshTomato ({self._entry.data['host']})",
            manufacturer="FreshTomato Project",
        )
