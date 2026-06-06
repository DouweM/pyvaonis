"""Camera platform: the live view and the most-recent image from the telescope."""

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
    """Set up the live-view and latest-image cameras."""
    async_add_entities(
        [VaonisLiveCamera(entry.runtime_data), VaonisLatestCamera(entry.runtime_data)]
    )


class VaonisLiveCamera(VaonisEntity, Camera):
    """The current live-stacked frame as a camera snapshot (only while observing)."""

    _attr_translation_key = "live"
    _attr_icon = "mdi:image-filter-center-focus"

    def __init__(self, coordinator: VaonisCoordinator) -> None:
        """Initialise the camera entity."""
        VaonisEntity.__init__(self, coordinator, "live")
        Camera.__init__(self)

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the current live stacked frame, or None when idle.

        Live-view only (the frame from the running observation), fetched from the already-written
        file — not the slow on-demand render that can exceed HA's camera timeout while stacking, and
        not the status' stale ``previousOperations`` frames. Browse finished runs via the **Vaonis**
        media source / `vaonis recent`, or use the *Latest image* camera.
        """
        client = self.coordinator.client
        img = client.current_image()
        if img is None:
            return None
        try:
            return await client.download_file(img.ftp_path)
        except Exception:
            _LOGGER.debug("no current Vaonis image", exc_info=True)
            return None

    @property
    def available(self) -> bool:
        """Available only while a live frame exists (i.e. during an observation)."""
        return super().available and self.coordinator.client.current_image() is not None


class VaonisLatestCamera(VaonisEntity, Camera):
    """The most recent image: the live frame while observing, else the newest saved capture.

    Always shows *something* once the scope has ever captured, so a dashboard card is never blank.
    Whether it's live or archived is reflected in the ``source`` attribute (and in the Status
    sensor: ``observing`` ⇒ live, otherwise the picture is from a previous run).
    """

    _attr_translation_key = "latest"
    _attr_icon = "mdi:image-multiple"

    def __init__(self, coordinator: VaonisCoordinator) -> None:
        """Initialise the camera entity."""
        VaonisEntity.__init__(self, coordinator, "latest")
        Camera.__init__(self)
        self._source: str | None = None

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
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
