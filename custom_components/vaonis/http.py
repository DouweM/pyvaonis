"""HTTP proxy view so the HA media browser can fetch telescope images.

Live frames are on the telescope's HTTP server and saved files are on its FTP server; neither
is directly reachable by the browser. This view lets Home Assistant (which *can* reach the
telescope, via the bridge) fetch the bytes and stream them to the frontend.
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

_LOGGER = logging.getLogger(__name__)

URL = "/api/vaonis_media/{entry_id}/{kind}/{ref}"

# The media browser requests every thumbnail at once; cap how many image fetches hit the telescope
# concurrently so we don't overwhelm its lightweight server / the Wi-Fi bridge.
_MAX_CONCURRENT_FETCHES = 4

_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".tif": "image/tiff", ".jxl": "image/jxl"}


def _mime_for(path: str) -> str:
    return _MIME.get(posixpath.splitext(path)[1].lower(), "application/octet-stream")


def register_view(hass: HomeAssistant) -> None:
    """Register the proxy view once."""
    if hass.data.get(f"{DOMAIN}_view_registered"):
        return
    hass.http.register_view(VaonisMediaView(hass))
    hass.data[f"{DOMAIN}_view_registered"] = True


class VaonisMediaView(HomeAssistantView):
    """Stream a telescope image (live HTTP frame or saved FTP file) to the frontend."""

    url = URL
    name = "api:vaonis_media"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        """Store hass for entry/client lookup."""
        self.hass = hass
        self._semaphore = asyncio.Semaphore(_MAX_CONCURRENT_FETCHES)

    async def get(self, request: web.Request, entry_id: str, kind: str, ref: str) -> web.Response:
        """Resolve ``kind`` (``http``|``ftp``) + ``ref`` (b64) and return the image bytes."""
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None or getattr(entry, "runtime_data", None) is None:
            return web.Response(status=404)
        client = entry.runtime_data.client
        try:
            decoded = base64.urlsafe_b64decode(ref.encode()).decode()
            async with self._semaphore:  # don't hammer the scope with parallel thumbnail fetches
                if kind == "http":
                    data = await client.fetch_image(decoded)
                    content_type = "image/jpeg"
                elif kind == "ftp":
                    data = await client.download_file(decoded)
                    content_type = _mime_for(decoded)
                else:
                    return web.Response(status=404)
        except Exception:
            _LOGGER.debug("media proxy failed for %s/%s", kind, ref, exc_info=True)
            return web.Response(status=502)
        return web.Response(body=data, content_type=content_type)
