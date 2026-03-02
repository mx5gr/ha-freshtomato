"""Config flow for FreshTomato integration (full UI setup, no YAML needed)."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv

from .api import CannotConnect, FreshTomatoAPI, InvalidAuth
from .const import (
    CONF_HTTP_ID,
    CONF_TRACK_WIRED,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SSL,
    DEFAULT_TRACK_WIRED,
    DEFAULT_USERNAME,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required("host"): str,
        vol.Optional("port", default=DEFAULT_PORT): cv.port,
        vol.Optional("ssl", default=DEFAULT_SSL): bool,
        vol.Optional("verify_ssl", default=DEFAULT_VERIFY_SSL): bool,
        vol.Optional("username", default=DEFAULT_USERNAME): str,
        vol.Required("password"): str,
        vol.Required(CONF_HTTP_ID): str,
    }
)


class FreshTomatoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the UI config flow for FreshTomato."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step shown in the UI."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Prevent duplicate entries for the same router
            await self.async_set_unique_id(
                f"{user_input['host']}:{user_input.get('port', DEFAULT_PORT)}"
            )
            self._abort_if_unique_id_configured()

            errors = await _test_connection(user_input)
            if not errors:
                return self.async_create_entry(
                    title=f"FreshTomato ({user_input['host']})",
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> FreshTomatoOptionsFlow:
        return FreshTomatoOptionsFlow(config_entry)


class FreshTomatoOptionsFlow(config_entries.OptionsFlow):
    """Handle options (poll interval, wired tracking toggle)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self._config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    "scan_interval",
                    default=current.get("scan_interval", DEFAULT_SCAN_INTERVAL),
                ): vol.All(int, vol.Range(min=10, max=600)),
                vol.Optional(
                    CONF_TRACK_WIRED,
                    default=current.get(CONF_TRACK_WIRED, DEFAULT_TRACK_WIRED),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)


# ──────────────────────────────────────────────────────────────────────────────
# Helper
# ──────────────────────────────────────────────────────────────────────────────

async def _test_connection(user_input: dict[str, Any]) -> dict[str, str]:
    """Attempt a real connection and return an errors dict (empty = success)."""
    api = FreshTomatoAPI(
        host=user_input["host"],
        port=user_input.get("port", DEFAULT_PORT),
        http_id=user_input[CONF_HTTP_ID],
        username=user_input.get("username", DEFAULT_USERNAME),
        password=user_input["password"],
        ssl=user_input.get("ssl", DEFAULT_SSL),
        verify_ssl=user_input.get("verify_ssl", DEFAULT_VERIFY_SSL),
    )
    try:
        await api.test_connection()
    except InvalidAuth:
        return {"base": "invalid_auth"}
    except CannotConnect:
        return {"base": "cannot_connect"}
    except Exception:  # pylint: disable=broad-except
        _LOGGER.exception("Unexpected error during FreshTomato setup test")
        return {"base": "unknown"}
    finally:
        await api.close()
    return {}
