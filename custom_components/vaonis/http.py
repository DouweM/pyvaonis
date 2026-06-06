"""HTTP proxy view so the HA media browser can fetch telescope images.

Live frames are on the telescope's HTTP server and saved files are on its FTP server; neither is
directly reachable by the browser. This view lets Home Assistant (which *can* reach the telescope,
via the bridge) fetch the bytes and stream them to the frontend — served from the shared
:class:`~.media_cache.MediaCache` (memory + disk) so a frame is pulled off the slow link at most once.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import posixpath

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .media_cache import MediaCache

_LOGGER = logging.getLogger(__name__)

URL = "/api/vaonis_media/{entry_id}/{kind}/{ref}"
CACHE_KEY = f"{DOMAIN}_media_cache"

# The media browser requests every thumbnail at once; cap how many image fetches hit the telescope
# concurrently so we don't overwhelm its lightweight server / the Wi-Fi bridge.
_MAX_CONCURRENT_FETCHES = 4

_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".tif": "image/tiff", ".jxl": "image/jxl"}


def _mime_for(path: str) -> str:
    return _MIME.get(posixpath.splitext(path)[1].lower(), "application/octet-stream")


def get_media_cache(hass: HomeAssistant) -> MediaCache:
    """The shared media cache, created on first use (also used by the image entity)."""
    cache = hass.data.get(CACHE_KEY)
    if cache is None:
        cache = hass.data[CACHE_KEY] = MediaCache(hass, hass.config.path(f"{DOMAIN}_media_cache"))
    return cache


def register_view(hass: HomeAssistant) -> None:
    """Register the proxy view once."""
    if hass.data.get(f"{DOMAIN}_view_registered"):
        return
    hass.http.register_view(VaonisMediaView(hass, get_media_cache(hass)))
    hass.data[f"{DOMAIN}_view_registered"] = True


class VaonisMediaView(HomeAssistantView):
    """Stream a telescope image (live HTTP frame or saved FTP file) to the frontend, with caching."""

    url = URL
    name = "api:vaonis_media"
    requires_auth = True

    def __init__(self, hass: HomeAssistant, cache: MediaCache) -> None:
        """Store hass + the shared cache for entry/client lookup."""
        self.hass = hass
        self._cache = cache
        self._semaphore = asyncio.Semaphore(_MAX_CONCURRENT_FETCHES)

    async def get(self, request: web.Request, entry_id: str, kind: str, ref: str) -> web.Response:
        """Resolve ``kind`` (``http``|``ftp``|``cover``) + ``ref`` (b64) and return image bytes."""
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None or getattr(entry, "runtime_data", None) is None:
            return web.Response(status=404)
        if kind not in ("http", "ftp", "cover"):
            return web.Response(status=404)
        try:
            decoded = base64.urlsafe_b64decode(ref.encode()).decode()
        except Exception:
            return web.Response(status=400)
        # Content type is deterministic from kind/path (covers/live frames are always JPEG), so a
        # cache hit needn't re-derive it from the source.
        content_type = _mime_for(decoded) if kind == "ftp" else "image/jpeg"
        cache_key = f"{entry_id}:{kind}:{ref}"

        if (data := await self._cache.get(cache_key)) is not None:
            return web.Response(body=data, content_type=content_type)

        client = entry.runtime_data.client  # miss: fetch from the scope (slow), then cache
        try:
            async with self._semaphore:  # don't hammer the scope with parallel thumbnail fetches
                if kind == "http":
                    data = await client.fetch_image(decoded)
                elif kind == "cover":
                    # A folder cover: the newest frame of one observation (decoded = its storeId dir).
                    frames = await client.observation_frames(decoded)
                    if not frames:
                        return web.Response(status=404)
                    data = await client.download_file(frames[0].path)
                else:
                    data = await client.download_file(decoded)
        except Exception:
            _LOGGER.debug("media proxy failed for %s/%s", kind, ref, exc_info=True)
            return web.Response(status=502)

        await self._cache.put(cache_key, data)
        return web.Response(body=data, content_type=content_type)
