"""Push-based DataUpdateCoordinator for the Stellina integration.

The telescope streams status over socket.io, so this coordinator connects once and pushes each
incoming status into Home Assistant via ``async_set_updated_data`` rather than polling. It connects
**read-only** — it does NOT take control — so HA can monitor while the phone app stays in control;
control is acquired on demand only when an action (a button/service/select) needs it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import CONF_HOST
from .const import CONF_MODEL
from .const import DOMAIN
from .pyvaonis import VaonisClient
from .pyvaonis import VaonisError
from .pyvaonis import VaonisStatus

_LOGGER = logging.getLogger(__name__)

type VaonisConfigEntry = ConfigEntry[VaonisCoordinator]


class VaonisCoordinator(DataUpdateCoordinator[VaonisStatus]):
    """Manage the socket.io connection and push status updates."""

    def __init__(self, hass: HomeAssistant, entry: VaonisConfigEntry) -> None:
        """Initialise the coordinator."""
        # Status arrives via push (socket.io); the interval is just a watchdog that detects a dropped
        # connection and reconnects when the scope is powered back on (e.g. after a shutdown), so the
        # entities recover without a reload. When connected it's a cheap no-op (returns the last push).
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=timedelta(seconds=30)
        )
        self.client = VaonisClient(
            ip=entry.data[CONF_HOST],
            session=async_get_clientsession(hass),
            # Identify as "Home Assistant" (shown in the app + the Controlling device sensor) with a
            # device_id stable across restarts — otherwise the default MAC/random id changes each run
            # and the scope's connectedDevices list piles up duplicate entries.
            device_id=f"home-assistant-{entry.entry_id}",
            name="Home Assistant",
        )
        self.client.on_status(self._handle_status)
        self.plan_task: asyncio.Task[None] | None = None
        # The target chosen in the select but not yet started; the Observe button reads this.
        self.selected_target: str | None = None
        # The object shown in the Latest image (set by the image entity's background fetch); the
        # "Latest target" sensor reads this so it can be rendered alongside the image.
        self.latest_target: str | None = None
        # UI-set Advanced-observation options the Observe button applies (switches/number entities
        # write these; they persist via those entities' restored state).
        self.mosaic_enabled: bool = False
        self.mosaic_width: float = 1.6
        self.mosaic_height: float = 1.1
        self.multi_night_enabled: bool = False
        # Signature of an init failure the user has moved past (dismissed by taking the next action),
        # so the "Initialization failed" state clears instead of lingering until a successful init.
        self._init_failure_ack: str | None = None

    @property
    def model(self) -> str | None:
        """The telescope model — live status when connected, else the value remembered in the config
        entry (so capability gating + the device model stay correct across an offline load)."""
        if self.data and self.data.model:
            return self.data.model
        return self.config_entry.data.get(CONF_MODEL) if self.config_entry else None

    def _remember_model(self, status: VaonisStatus) -> None:
        """Persist the learned model on the config entry so it's known on the next offline load."""
        model = status.model
        entry = self.config_entry
        if not model or not entry or entry.data.get(CONF_MODEL) == model:
            return
        self.hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_MODEL: model})
        # If the entry was already loaded with a different/absent model (e.g. an offline load that
        # created the permissive entity set), reload so capability gating re-runs for the real model.
        if entry.state is ConfigEntryState.LOADED:
            self.hass.async_create_task(self.hass.config_entries.async_reload(entry.entry_id))

    def _active_init_failure(self) -> tuple[str | None, str] | None:
        """The current (unacknowledged) init failure as ``(short, detail)``, or None.

        Surfaces ``previousOperations.autoInit.error`` (which the firmware leaves after a failed init,
        and which the status headline would otherwise show as plain "Idle"). Clears once the user
        takes the next action (Close arm, Initialize again, …) — see :meth:`_ack_init_failure`."""
        failure = self.client.autoinit_failure()
        if failure is None or failure[0] == self._init_failure_ack:
            return None
        return failure[1], failure[2]  # (short, detail)

    @property
    def init_failure(self) -> str | None:
        """Full reason the last auto-init failed (e.g. not enough stars), or None."""
        active = self._active_init_failure()
        return active[1] if active else None

    @property
    def init_failure_summary(self) -> str | None:
        """One-line headline for the Status sensor (terse reason), or None when no failure."""
        active = self._active_init_failure()
        if active is None:
            return None
        return f"Initialization failed: {active[0]}" if active[0] else "Initialization failed"

    def _ack_init_failure(self) -> None:
        """Dismiss the currently-shown init failure (called after any control action)."""
        failure = self.client.autoinit_failure()
        if failure and failure[0] != self._init_failure_ack:
            self._init_failure_ack = failure[0]
            self.async_update_listeners()

    def _handle_status(self, status: VaonisStatus) -> None:
        """Receive a pushed status from the telescope."""
        self.async_set_updated_data(status)
        self._remember_model(status)

    async def run_action(self, action: Callable[[VaonisClient], Awaitable[Any]]) -> Any:
        """One-shot control: take control, run ``action``, then release it (HA never holds control).

        Releasing does not stop a started observation/plan — the scope runs on autonomously — so the
        phone can take over again right after. Release failures (e.g. the link dropping after a
        shutdown) are ignored.
        """
        await self.client.take_control()
        try:
            return await action(self.client)
        finally:
            with contextlib.suppress(VaonisError):
                await self.client.release_control()
            self._ack_init_failure()  # taking any action moves past a prior init failure

    async def _async_update_data(self) -> VaonisStatus:
        """Connect (once), read-only; returns the current status. Control is taken on demand."""
        try:
            if not self.client.connected:
                status = await self.client.connect()
                self._remember_model(status)
                return status
        except VaonisError as err:
            raise UpdateFailed(str(err)) from err
        if self.data is None:  # pragma: no cover - defensive
            raise UpdateFailed("no status received yet")
        return self.data

    async def async_shutdown(self) -> None:
        """Tear down the connection."""
        await super().async_shutdown()
        await self.client.disconnect()
