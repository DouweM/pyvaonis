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
        """Return the latest stacked frame (current run, or most recent when idle).

        Fetches the already-written frame file (fast/static), not the slow on-demand render — the
        latter can exceed HA's camera-image timeout while the scope is stacking.
        """
        client = self.coordinator.client
        img = client.current_image()
        if img is None:
            recent = client.recent_images()
            img = recent[0] if recent else None
        if img is None:
            return None
        try:
            return await client.download_file(img.ftp_path)
        except Exception:
            _LOGGER.debug("no current Vaonis image", exc_info=True)
            return None

    @property
    def available(self) -> bool:
        """Available whenever a current or recent frame exists."""
        client = self.coordinator.client
        return super().available and (
            client.current_image() is not None or bool(client.recent_images())
        )
