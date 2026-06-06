"""HTTP proxy view so the HA media browser can fetch telescope images.

Live frames are on the telescope's HTTP server and saved files are on its FTP server; neither is
directly reachable by the browser. This view lets Home Assistant (which *can* reach the telescope,
via the bridge) fetch the bytes and stream them to the frontend.

FTP fetches are slow, so served frames are cached: a small in-memory LRU (this session) backed by a
**disk cache** that persists across restarts — mirroring how the Singularity app keeps local copies,
so a frame is pulled off the scope at most once. Saved frames never change, so the cache never
invalidates.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import logging
import os
import posixpath
from collections import OrderedDict

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

URL = "/api/vaonis_media/{entry_id}/{kind}/{ref}"

# The media browser requests every thumbnail at once; cap how many image fetches hit the telescope
# concurrently so we don't overwhelm its lightweight server / the Wi-Fi bridge.
_MAX_CONCURRENT_FETCHES = 4

_MEM_CACHE_MAX = 64  # in-session hot cache (count)
_DISK_CACHE_MAX = 1500  # frames kept on disk before the oldest are pruned

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
    """Stream a telescope image (live HTTP frame or saved FTP file) to the frontend, with caching."""

    url = URL
    name = "api:vaonis_media"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        """Store hass for entry/client lookup."""
        self.hass = hass
        self._semaphore = asyncio.Semaphore(_MAX_CONCURRENT_FETCHES)
        self._mem: OrderedDict[str, bytes] = OrderedDict()
        self._cache_dir = hass.config.path(f"{DOMAIN}_media_cache")

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
        # Content type is deterministic from kind/path, so cache hits needn't re-derive it from the
        # source (covers/live frames are always JPEG).
        content_type = _mime_for(decoded) if kind == "ftp" else "image/jpeg"
        cache_key = f"{entry_id}:{kind}:{ref}"
        disk_path = self._disk_path(cache_key)

        if (data := self._mem.get(cache_key)) is not None:  # L1: in-session memory
            self._mem.move_to_end(cache_key)
            return web.Response(body=data, content_type=content_type)

        data = await self.hass.async_add_executor_job(self._read_disk, disk_path)  # L2: disk
        if data is not None:
            self._mem_put(cache_key, data)
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

        self._mem_put(cache_key, data)
        await self.hass.async_add_executor_job(self._write_disk, disk_path, data)
        return web.Response(body=data, content_type=content_type)

    def _mem_put(self, key: str, data: bytes) -> None:
        self._mem[key] = data
        self._mem.move_to_end(key)
        while len(self._mem) > _MEM_CACHE_MAX:
            self._mem.popitem(last=False)

    def _disk_path(self, cache_key: str) -> str:
        return os.path.join(self._cache_dir, hashlib.sha1(cache_key.encode()).hexdigest())

    def _read_disk(self, path: str) -> bytes | None:
        try:
            with open(path, "rb") as fh:
                return fh.read()
        except OSError:
            return None

    def _write_disk(self, path: str, data: bytes) -> None:
        try:
            os.makedirs(self._cache_dir, exist_ok=True)
            tmp = f"{path}.{os.getpid()}.tmp"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)  # atomic; readers never see a partial file
        except OSError:
            _LOGGER.debug("media disk-cache write failed for %s", path, exc_info=True)
            return
        self._prune_disk()

    def _prune_disk(self) -> None:
        """Keep the disk cache bounded — drop the oldest files past the cap (by mtime)."""
        try:
            files = [os.path.join(self._cache_dir, n) for n in os.listdir(self._cache_dir)]
        except OSError:
            return
        if len(files) <= _DISK_CACHE_MAX:
            return
        files.sort(key=lambda p: os.path.getmtime(p))
        for path in files[: len(files) - _DISK_CACHE_MAX]:
            with contextlib.suppress(OSError):
                os.remove(path)
