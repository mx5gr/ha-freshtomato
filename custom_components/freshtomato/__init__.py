"""FreshTomato router integration for Home Assistant 2026.2+."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .api import FreshTomatoAPI
from .const import (
    CONF_HTTP_ID,
    CONF_TRACK_WIRED,
    DATA_COORDINATOR,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SSL,
    DEFAULT_TRACK_WIRED,
    DEFAULT_USERNAME,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import FreshTomatoCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up FreshTomato from a config entry (UI-created)."""

    # Build options with fallback to defaults
    options = entry.options
    scan_interval = int(options.get("scan_interval", DEFAULT_SCAN_INTERVAL))

    api = FreshTomatoAPI(
        host=entry.data["host"],
        port=entry.data.get("port", DEFAULT_PORT),
        http_id=entry.data[CONF_HTTP_ID],
        username=entry.data.get("username", DEFAULT_USERNAME),
        password=entry.data["password"],
        ssl=entry.data.get("ssl", DEFAULT_SSL),
        verify_ssl=entry.data.get("verify_ssl", DEFAULT_VERIFY_SSL),
    )

    coordinator = FreshTomatoCoordinator(
        hass, api, entry, scan_interval=scan_interval
    )

    # Fetch initial data — raises ConfigEntryNotReady on failure
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {DATA_COORDINATOR: coordinator}

    # Forward setup to each platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload entry when options change (e.g. scan_interval updated)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: FreshTomatoCoordinator = hass.data[DOMAIN][entry.entry_id][
            DATA_COORDINATOR
        ]
        await coordinator.api.close()
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload integration when options are changed in the UI."""
    await hass.config_entries.async_reload(entry.entry_id)
