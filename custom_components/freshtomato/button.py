"""Button platform for FreshTomato integration.

Buttons:
  • Reboot Router
  • Renew WAN DHCP Lease
  • Enable 2.4 GHz Radio   (toggle → use switch.py instead in real use)
  • Disable 2.4 GHz Radio
  • Enable 5 GHz Radio
  • Disable 5 GHz Radio

Note: Reboot and DHCP renew are destructive/stateful actions, so they are
best modelled as Buttons rather than Switches in HA 2026.2.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
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
class FreshTomatoButtonDescription(ButtonEntityDescription):
    action: Any = None  # Callable[[FreshTomatoAPI], Coroutine]


BUTTONS: tuple[FreshTomatoButtonDescription, ...] = (
    FreshTomatoButtonDescription(
        key="reboot",
        name="Reboot Router",
        icon="mdi:restart",
        action=lambda api: api.reboot(),
    ),
    FreshTomatoButtonDescription(
        key="dhcp_renew",
        name="Renew WAN DHCP Lease",
        icon="mdi:refresh-circle",
        action=lambda api: api.dhcp_renew(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: FreshTomatoCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities(
        FreshTomatoButton(coordinator, entry, desc)
        for desc in BUTTONS
    )


class FreshTomatoButton(
    CoordinatorEntity[FreshTomatoCoordinator], ButtonEntity
):
    """A button entity that triggers an action on the FreshTomato router."""

    entity_description: FreshTomatoButtonDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FreshTomatoCoordinator,
        entry: ConfigEntry,
        description: FreshTomatoButtonDescription,
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
            if self.coordinator.data
            else "Router",
        )

    async def async_press(self) -> None:
        """Execute the button action."""
        fn = self.entity_description.action
        if fn is None:
            return
        try:
            await fn(self.coordinator.api)
            _LOGGER.debug("FreshTomato button pressed: %s", self.entity_description.key)
            # Trigger a coordinator refresh to pick up new state
            await self.coordinator.async_request_refresh()
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error(
                "FreshTomato button action failed (%s): %s",
                self.entity_description.key, err,
            )
