"""Shared image cache for the media browser + proactive capture download.

The link to the telescope is slow (~25 KB/s over the Wi-Fi bridge), so a frame should be pulled off
it at most once, ever. This is a two-level cache: an in-session memory LRU backed by an on-disk store
under ``<config>/vaonis_media_cache/`` that persists across restarts. Saved frames never change, so it
never invalidates. The HTTP proxy reads/writes it; the image entity *pre-populates* it with each live
frame it already fetches during an observation, so that observation's gallery is instant later.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import logging
import os
from collections import OrderedDict

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_MEM_MAX = 64  # in-session hot cache (count)
_DISK_MAX = 1500  # frames kept on disk before the oldest are pruned (by mtime)


def frame_key(entry_id: str, ftp_path: str) -> str:
    """Cache key for a saved frame — matches what the media browser/proxy use (``ftp`` kind).

    The browser builds identifier ``<entry_id>|ftp|<b64 path>`` and the proxy keys on
    ``<entry_id>:ftp:<b64 path>``; the capture downloader reuses this so its pre-fetched frames are
    found on the exact same key.
    """
    ref = base64.urlsafe_b64encode(ftp_path.encode()).decode()
    return f"{entry_id}:ftp:{ref}"


class MediaCache:
    """In-memory LRU backed by a persistent on-disk cache of image bytes."""

    def __init__(self, hass: HomeAssistant, cache_dir: str) -> None:
        """Initialise with the on-disk cache directory."""
        self.hass = hass
        self._dir = cache_dir
        self._mem: OrderedDict[str, bytes] = OrderedDict()

    async def get(self, key: str) -> bytes | None:
        """Return cached bytes (memory, then disk), or None on a miss."""
        if (data := self._mem.get(key)) is not None:
            self._mem.move_to_end(key)
            return data
        data = await self.hass.async_add_executor_job(self._read, self._path(key))
        if data is not None:
            self._mem_put(key, data)
        return data

    async def has(self, key: str) -> bool:
        """Whether the key is already cached (cheap — memory or a disk stat)."""
        if key in self._mem:
            return True
        return await self.hass.async_add_executor_job(os.path.exists, self._path(key))

    async def put(self, key: str, data: bytes) -> None:
        """Store bytes in memory and on disk."""
        self._mem_put(key, data)
        await self.hass.async_add_executor_job(self._write, self._path(key), data)

    def _mem_put(self, key: str, data: bytes) -> None:
        self._mem[key] = data
        self._mem.move_to_end(key)
        while len(self._mem) > _MEM_MAX:
            self._mem.popitem(last=False)

    def _path(self, key: str) -> str:
        return os.path.join(self._dir, hashlib.sha1(key.encode()).hexdigest())

    def _read(self, path: str) -> bytes | None:
        try:
            with open(path, "rb") as fh:
                return fh.read()
        except OSError:
            return None

    def _write(self, path: str, data: bytes) -> None:
        try:
            os.makedirs(self._dir, exist_ok=True)
            tmp = f"{path}.{os.getpid()}.tmp"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)  # atomic; readers never see a partial file
        except OSError:
            _LOGGER.debug("media disk-cache write failed for %s", path, exc_info=True)
            return
        self._prune()

    def _prune(self) -> None:
        """Keep the disk cache bounded — drop the oldest files past the cap (by mtime)."""
        try:
            files = [os.path.join(self._dir, n) for n in os.listdir(self._dir)]
        except OSError:
            return
        if len(files) <= _DISK_MAX:
            return
        files.sort(key=lambda p: os.path.getmtime(p))
        for path in files[: len(files) - _DISK_MAX]:
            with contextlib.suppress(OSError):
                os.remove(path)
