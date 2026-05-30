"""Push-based DataUpdateCoordinator for the Stellina integration.

The telescope streams status over socket.io, so this coordinator connects once and pushes each
incoming status into Home Assistant via ``async_set_updated_data`` rather than polling. It connects
**read-only** — it does NOT take control — so HA can monitor while the phone app stays in control;
control is acquired on demand only when an action (a button/service/select) needs it.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.update_coordinator import UpdateFailed

from pystellina import StellinaClient
from pystellina import StellinaError
from pystellina import StellinaStatus

from .const import CONF_HOST
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

type StellinaConfigEntry = ConfigEntry[StellinaCoordinator]


class StellinaCoordinator(DataUpdateCoordinator[StellinaStatus]):
    """Manage the socket.io connection and push status updates."""

    def __init__(self, hass: HomeAssistant, entry: StellinaConfigEntry) -> None:
        """Initialise the coordinator."""
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=None)
        self.client = StellinaClient(
            ip=entry.data[CONF_HOST],
            session=async_get_clientsession(hass),
        )
        self.client.on_status(self._handle_status)
        self.plan_task: asyncio.Task[None] | None = None

    def _handle_status(self, status: StellinaStatus) -> None:
        """Receive a pushed status from the telescope."""
        self.async_set_updated_data(status)

    async def _async_update_data(self) -> StellinaStatus:
        """Connect (once), read-only; returns the current status. Control is taken on demand."""
        try:
            if not self.client.connected:
                return await self.client.connect()
        except StellinaError as err:
            raise UpdateFailed(str(err)) from err
        if self.data is None:  # pragma: no cover - defensive
            raise UpdateFailed("no status received yet")
        return self.data

    async def async_shutdown(self) -> None:
        """Tear down the connection."""
        await super().async_shutdown()
        await self.client.disconnect()
