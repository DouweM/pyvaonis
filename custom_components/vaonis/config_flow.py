"""Config flow for the Stellina integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from pyvaonis import VaonisClient
from pyvaonis import VaonisError

from .const import CONF_HOST
from .const import DEFAULT_HOST
from .const import DEFAULT_NAME
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema({vol.Required(CONF_HOST, default=DEFAULT_HOST): str})


class VaonisConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Stellina."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step: connect to confirm reachability."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            client = VaonisClient(ip=host, session=async_get_clientsession(self.hass))
            try:
                status = await client.connect()
            except VaonisError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error connecting to Stellina")
                errors["base"] = "unknown"
            else:
                unique_id = status.telescope_id or host
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=DEFAULT_NAME, data={CONF_HOST: host})
            finally:
                await client.disconnect()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )
