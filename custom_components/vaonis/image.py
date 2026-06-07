"""Image platform: the most recent frame from the telescope.

A single image entity (telescope stacks are slow stills, not a video feed): it shows the live frame
while observing, otherwise the newest saved capture. The ``source`` attribute says live vs archived,
and ``target`` names the object (mirrored on the coordinator for the "Latest target" sensor).

The frame is fetched in the background and cached, so ``async_image`` returns instantly — fetching
inline would risk exceeding Home Assistant's 10 s image-proxy timeout. Bytes come over HTTP (the
``/files`` static server) rather than FTP; FTP is used only to *list* the capture library.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant
from homeassistant.core import callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .coordinator import VaonisConfigEntry
from .coordinator import VaonisCoordinator
from .entity import VaonisEntity
from .http import get_media_cache
from .media_cache import MediaCache
from .media_cache import frame_key
from .pyvaonis import observation_object_name

_LOGGER = logging.getLogger(__name__)


@dataclass
class _ImageRestoreData(ExtraStoredData):
    """What we persist so the Latest image survives an HA restart while offline.

    The frame *bytes* already live in the on-disk media cache; we just remember which key to reload
    (the cache keys are hashed, so they can't be scanned for "the newest"), plus the metadata needed
    to redraw the card without the telescope.
    """

    cache_key: str
    source: str | None
    target: str | None
    last_updated: str | None  # ISO 8601

    def as_dict(self) -> dict[str, Any]:
        """Serialise for HA's restore store."""
        return {
            "cache_key": self.cache_key,
            "source": self.source,
            "target": self.target,
            "last_updated": self.last_updated,
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VaonisConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the telescope image entity."""
    async_add_entities([VaonisImage(hass, entry.runtime_data)])


class VaonisImage(VaonisEntity, ImageEntity, RestoreEntity):
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
        self._target: str | None = None
        self._frame_path: str | None = None  # FTP path of the last-fetched frame, for the cache key
        self._cache_key: str | None = None  # disk-cache key of the shown frame (for restore)
        self._media_cache: MediaCache | None = None
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
        """Restore the last frame from the disk cache, then kick off a fresh fetch."""
        await super().async_added_to_hass()
        self._media_cache = get_media_cache(self.hass)
        await self._restore_cached_frame()
        self._signature = self._signature_now()
        self._request_refresh()

    @property
    def extra_restore_state_data(self) -> _ImageRestoreData | None:
        """Persist the shown frame's cache key + metadata so it survives a restart while offline."""
        if not self._cache_key:
            return None
        last_updated = (
            self._attr_image_last_updated.isoformat() if self._attr_image_last_updated else None
        )
        return _ImageRestoreData(self._cache_key, self._source, self._target, last_updated)

    async def _restore_cached_frame(self) -> None:
        """Reload the last-shown frame from the on-disk cache (so it's there even before/without a
        live connection). A successful live fetch overwrites it moments later when online."""
        if self._media_cache is None:
            return
        last = await self.async_get_last_extra_data()
        if last is None:
            return
        stored = last.as_dict()
        key = stored.get("cache_key")
        if not key or self._cached is not None:
            return
        cached = await self._media_cache.get(key)
        if cached is None:
            return
        self._cached = cached
        self._cache_key = key
        self._source = stored.get("source")
        self._target = stored.get("target")
        if self._target:
            self.coordinator.latest_target = self._target
        parsed = dt_util.parse_datetime(stored.get("last_updated") or "")
        self._attr_image_last_updated = parsed or dt_util.utcnow()
        self.async_write_ha_state()
        self.coordinator.async_update_listeners()  # let the Latest target sensor pick up the name

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
            if result is not None:
                data, taken_at = result
                target_changed = self._target != self.coordinator.latest_target
                self.coordinator.latest_target = self._target
                if data != self._cached:
                    self._cached = data
                    # Stamp with when the frame was actually taken (an archived frame reads
                    # "5 days ago", not "now"); a changed timestamp also tells the frontend to refetch.
                    self._attr_image_last_updated = taken_at or dt_util.utcnow()
                    self.async_write_ha_state()
                    # Proactively populate the gallery cache: we just downloaded this frame for
                    # display, so persisting it (free) makes it instant in the media browser later —
                    # especially each live frame during an observation, like the app does.
                    entry = self.coordinator.config_entry
                    if self._media_cache is not None and self._frame_path and entry:
                        self._cache_key = frame_key(entry.entry_id, self._frame_path)
                        await self._media_cache.put(self._cache_key, data)
                if target_changed:  # refresh the Latest target sensor, which reads the coordinator
                    self.coordinator.async_update_listeners()
        finally:
            self._refreshing = False
        if self._stale:  # a change landed while we were fetching — go again
            self._stale = False
            self._request_refresh()

    async def _fetch(self) -> tuple[bytes, datetime | None] | None:
        """Fetch the live frame if observing, otherwise the newest finished capture (over FTP).

        FTP is the transport that actually serves saved frames here — the scope's ``/files`` HTTP
        server only renders the *live* capture, not arbitrary archived files over the bridge.
        """
        client = self.coordinator.client
        img = client.current_image()
        if img is not None:
            try:
                data = await client.download_file(img.ftp_path)
                self._source = "live"
                self._frame_path = img.ftp_path
                obs = client.current_observation()
                self._target = obs.object_name if obs else None
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
                self._frame_path = frame.path
                segments = frame.path.split("/")
                store_id = (
                    segments[segments.index("captures") + 1] if "captures" in segments else ""
                )
                self._target = observation_object_name(store_id)
                return data, frame.modified  # the saved file's real modify time (UTC)
        except Exception:
            _LOGGER.debug("no saved Vaonis capture to show", exc_info=True)
        return None

    @property
    def available(self) -> bool:
        """Keep showing the last frame even while the scope is unreachable — async_image serves the
        cached bytes (no live connection needed); only Unavailable if we've never loaded one."""
        return super().available or self._cached is not None

    async def async_image(self) -> bytes | None:
        """Return the cached frame (fetched in the background by :meth:`_refresh`)."""
        return self._cached

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Expose the frame source (live|archived) and its target object for dashboards."""
        attrs: dict[str, str] = {}
        if self._source:
            attrs["source"] = self._source
        if self._target:
            attrs["target"] = self._target
        return attrs
