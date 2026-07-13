"""Config and options flow for Ambient Weather Local."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .console import ConsoleClient, ConsoleError
from .const import (
    CONF_CONSOLE_IP,
    CONF_DEVICE_NAME,
    CONF_LISTEN_PORT,
    CONF_SCAN_MINUTES,
    DEFAULT_DEVICE_NAME,
    DEFAULT_LISTEN_PORT,
    DEFAULT_SCAN_MINUTES,
    DOMAIN,
)


class AmbientConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            client = ConsoleClient(
                async_get_clientsession(self.hass), user_input[CONF_CONSOLE_IP]
            )
            try:
                settings = await client.get_settings()
            except ConsoleError:
                errors["base"] = "cannot_connect"
            else:
                mac = settings.get("sta_mac")
                if mac:
                    await self.async_set_unique_id(mac.lower())
                    self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME),
                    data={
                        CONF_CONSOLE_IP: user_input[CONF_CONSOLE_IP],
                        CONF_LISTEN_PORT: user_input[CONF_LISTEN_PORT],
                        CONF_DEVICE_NAME: user_input.get(
                            CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME
                        ),
                        CONF_SCAN_MINUTES: user_input[CONF_SCAN_MINUTES],
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_CONSOLE_IP): str,
                vol.Required(
                    CONF_DEVICE_NAME, default=DEFAULT_DEVICE_NAME
                ): str,
                vol.Required(
                    CONF_LISTEN_PORT, default=DEFAULT_LISTEN_PORT
                ): int,
                vol.Required(
                    CONF_SCAN_MINUTES, default=DEFAULT_SCAN_MINUTES
                ): int,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return AmbientOptionsFlow()


class AmbientOptionsFlow(OptionsFlow):
    """Adjust port / scan interval / device name after setup."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        data = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_DEVICE_NAME,
                    default=data.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME),
                ): str,
                vol.Required(
                    CONF_LISTEN_PORT,
                    default=data.get(CONF_LISTEN_PORT, DEFAULT_LISTEN_PORT),
                ): int,
                vol.Required(
                    CONF_SCAN_MINUTES,
                    default=data.get(CONF_SCAN_MINUTES, DEFAULT_SCAN_MINUTES),
                ): int,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
