"""Image platform: the most recent frame from the telescope.

A single image entity (telescope stacks are slow stills, not a video feed): it shows the live frame
while observing, otherwise the newest saved capture over FTP. The ``source`` attribute (and the Status
sensor) says whether you're looking at a live or an archived frame.
"""

from __future__ import annotations

import logging

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
        self._source: str | None = None

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
        """Seed the signature + timestamp so a frame is fetched immediately, not only on next push."""
        await super().async_added_to_hass()
        self._signature = self._signature_now()
        self._attr_image_last_updated = dt_util.utcnow()

    @callback
    def _handle_coordinator_update(self) -> None:
        signature = self._signature_now()
        if signature != self._signature:
            self._signature = signature
            self._attr_image_last_updated = dt_util.utcnow()  # tells the frontend to refetch
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        """Return the live frame if observing, otherwise the newest finished capture over FTP."""
        client = self.coordinator.client
        img = client.current_image()
        if img is not None:
            try:
                data = await client.download_file(img.ftp_path)
                self._source = "live"
                return data
            except Exception:
                _LOGGER.debug(
                    "live frame unavailable, falling back to saved capture", exc_info=True
                )
        try:
            frame = await client.latest_capture_path()
            if frame is not None:
                data = await client.download_file(frame)
                self._source = "archived"
                return data
        except Exception:
            _LOGGER.debug("no saved Vaonis capture to show", exc_info=True)
        self._source = None
        return None

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Whether the last-served image was the live frame or a saved capture."""
        return {"source": self._source} if self._source else {}
