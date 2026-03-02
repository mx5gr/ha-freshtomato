"""Switch platform for FreshTomato integration.

Switches:
  • 2.4 GHz Radio  – enable / disable the 2.4 GHz wireless radio
  • 5 GHz Radio    – enable / disable the 5 GHz wireless radio

State is read from nvram (wl0_radio / wl1_radio).
Write is sent via wlradio.cgi.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import FreshTomatoAPI
from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import FreshTomatoCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class FreshTomatoSwitchDescription(SwitchEntityDescription):
    nvram_key: str = ""          # NVRAM key holding current state ("1"=on)
    radio_unit: int = 0          # Passed to toggle_wifi_radio()


SWITCHES: tuple[FreshTomatoSwitchDescription, ...] = (
    FreshTomatoSwitchDescription(
        key="wl0_radio",
        name="2.4 GHz Radio",
        icon="mdi:wifi",
        nvram_key="wl0_radio",
        radio_unit=0,
    ),
    FreshTomatoSwitchDescription(
        key="wl1_radio",
        name="5 GHz Radio",
        icon="mdi:wifi",
        nvram_key="wl1_radio",
        radio_unit=1,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: FreshTomatoCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities(
        FreshTomatoRadioSwitch(coordinator, entry, desc) for desc in SWITCHES
    )


class FreshTomatoRadioSwitch(
    CoordinatorEntity[FreshTomatoCoordinator], SwitchEntity
):
    """Switch entity to enable/disable a wireless radio."""

    entity_description: FreshTomatoSwitchDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FreshTomatoCoordinator,
        entry: ConfigEntry,
        description: FreshTomatoSwitchDescription,
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
        if not self.coordinator.data:
            return None
        val = self.coordinator.data.nvram.get(self.entity_description.nvram_key)
        if val is None:
            return None
        return val.strip() == "1"

    @property
    def available(self) -> bool:
        if not super().available or not self.coordinator.data:
            return False
        # Unavailable if nvram hasn't been fetched yet
        return self.entity_description.nvram_key in self.coordinator.data.nvram

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.api.toggle_wifi_radio(
            self.entity_description.radio_unit, True
        )
        # Optimistically update nvram cache so state reflects immediately
        self.coordinator._nvram_cache[self.entity_description.nvram_key] = "1"  # noqa: SLF001
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.api.toggle_wifi_radio(
            self.entity_description.radio_unit, False
        )
        self.coordinator._nvram_cache[self.entity_description.nvram_key] = "0"  # noqa: SLF001
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
