"""Camera platform: the live, progressively-stacked view from the telescope."""

from __future__ import annotations

import logging

from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import VaonisConfigEntry
from .coordinator import VaonisCoordinator
from .entity import VaonisEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VaonisConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the live-view camera."""
    async_add_entities([VaonisCamera(entry.runtime_data)])


class VaonisCamera(VaonisEntity, Camera):
    """The current live-stacked frame as a camera snapshot."""

    _attr_translation_key = "live"

    def __init__(self, coordinator: VaonisCoordinator) -> None:
        """Initialise the camera entity."""
        VaonisEntity.__init__(self, coordinator, "live")
        Camera.__init__(self)

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the latest stacked frame, or None when idle."""
        try:
            return await self.coordinator.client.fetch_current_image()
        except Exception:
            _LOGGER.debug("no current Stellina image", exc_info=True)
            return None

    @property
    def available(self) -> bool:
        """Available while an observation with a live frame is in progress."""
        return super().available and self.coordinator.client.current_image() is not None
