"""Image platform: the most recent frame from the telescope.

A single image entity (telescope stacks are slow stills, not a video feed): it shows the live frame
while observing, otherwise the newest saved capture over FTP. The ``source`` attribute (and the Status
sensor) says whether you're looking at a live or an archived frame.

The frame is fetched in the background and cached, so ``async_image`` returns instantly — fetching
inline would risk exceeding Home Assistant's 10 s image-proxy timeout (the idle path lists the FTP
capture library and downloads a JPEG over the Wi-Fi bridge, which can be slow).
"""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant
from homeassistant.core import callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import VaonisConfigEntry
from .coordinator import VaonisCoordinator
from .entity import VaonisEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VaonisConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the telescope image entity."""
    async_add_entities([VaonisImage(hass, entry.runtime_data)])


class VaonisImage(VaonisEntity, ImageEntity):
    """The most recent telescope frame (live while observing, else the newest saved capture)."""

    _attr_translation_key = "image"
    _attr_icon = "mdi:telescope"

    def __init__(self, hass: HomeAssistant, coordinator: VaonisCoordinator) -> None:
        """Initialise the image entity."""
        VaonisEntity.__init__(self, coordinator, "image")
        ImageEntity.__init__(self, hass)
        self._signature: tuple[bool, int | None] | None = None
        self._cached: bytes | None = None
        self._source: str | None = None
        self._refreshing = False
        self._stale = False

    def _signature_now(self) -> tuple[bool, int | None]:
        """A cheap key (from status, no FTP) that changes whenever the shown image should refresh."""
        obs = self.coordinator.client.current_observation()
        if obs is not None:
            return True, obs.stacking_count  # new stacked frame while observing
        return (
            False,
            None,
        )  # idle — the transition itself triggers one refresh to the newest capture

    async def async_added_to_hass(self) -> None:
        """Kick off the first fetch so a frame is loaded without waiting for the next status push."""
        await super().async_added_to_hass()
        self._signature = self._signature_now()
        self._request_refresh()

    @callback
    def _handle_coordinator_update(self) -> None:
        signature = self._signature_now()
        if signature != self._signature:
            self._signature = signature
            self._request_refresh()
        super()._handle_coordinator_update()

    def _request_refresh(self) -> None:
        """Fetch the current frame in the background (coalescing concurrent requests)."""
        if self._refreshing:
            self._stale = True
            return
        self.hass.async_create_task(self._refresh())

    async def _refresh(self) -> None:
        self._refreshing = True
        try:
            result = await self._fetch()
            if result is not None and result[0] != self._cached:
                self._cached, taken_at = result
                # Stamp with when the frame was actually taken (an archived frame reads "5 days ago",
                # not "now"); a changed timestamp also tells the frontend to refetch.
                self._attr_image_last_updated = taken_at or dt_util.utcnow()
                self.async_write_ha_state()
        finally:
            self._refreshing = False
        if self._stale:  # a change landed while we were fetching — go again
            self._stale = False
            self._request_refresh()

    async def _fetch(self) -> tuple[bytes, datetime | None] | None:
        """Download the live frame if observing, otherwise the newest finished capture over FTP."""
        client = self.coordinator.client
        img = client.current_image()
        if img is not None:
            try:
                data = await client.download_file(img.ftp_path)
                self._source = "live"
                return data, dt_util.utcnow()  # live frame: just captured
            except Exception:
                _LOGGER.debug(
                    "live frame unavailable, falling back to saved capture", exc_info=True
                )
        try:
            frame = await client.latest_capture()
            if frame is not None:
                data = await client.download_file(frame.path)
                self._source = "archived"
                return data, frame.modified  # the saved file's real modify time (UTC)
        except Exception:
            _LOGGER.debug("no saved Vaonis capture to show", exc_info=True)
        return None

    async def async_image(self) -> bytes | None:
        """Return the cached frame (fetched in the background by :meth:`_refresh`)."""
        return self._cached

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Whether the last-served image was the live frame or a saved capture."""
        return {"source": self._source} if self._source else {}
