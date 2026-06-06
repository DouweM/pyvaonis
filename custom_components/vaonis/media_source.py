"""Media source: browse the telescope's observations in the HA media browser.

The telescope stores finished runs on its FTP server as
``/system/captures/<storeId>/images/IMG_NNNN.jpg`` (``storeId`` = ``<date>_observation_<objectId>``),
alongside ``images/`` nesting and ``store.json``/``capture.json`` metadata. Rather than expose that
raw tree, we present one folder per observation — labelled by object + date, newest first — whose
children are the frames (with thumbnails). A live frame appears on top while observing.

Identifiers are pipe-delimited: ``<entry_id>`` and ``<entry_id>|obs|<b64 storeId-path>`` for folders;
``<entry_id>|http|<b64 url>`` / ``<entry_id>|ftp|<b64 path>`` for leaves, which resolve to the proxy
view in :mod:`.http` that streams the bytes through Home Assistant.
"""

from __future__ import annotations

import base64
import logging
import re

from homeassistant.components.media_player import MediaClass
from homeassistant.components.media_source import BrowseMediaSource
from homeassistant.components.media_source import MediaSource
from homeassistant.components.media_source import MediaSourceItem
from homeassistant.components.media_source import PlayMedia
from homeassistant.components.media_source import Unresolvable
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import VaonisConfigEntry
from .http import _mime_for

# FTP_ROOT = "/system/captures" — where finished runs live (the device's /user is empty)
from .pyvaonis import observation_object_name
from .pyvaonis.const import FTP_ROOT

_LOGGER = logging.getLogger(__name__)

_MAX_FRAMES = 300  # cap frames listed per observation (newest first); logged if exceeded
_STORE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-\d{2}")


async def async_get_media_source(hass: HomeAssistant) -> VaonisMediaSource:
    """Set up the Vaonis media source."""
    return VaonisMediaSource(hass)


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _observation_label(store_id: str) -> str:
    """Turn a storeId into a friendly ``<Object> · <date> <time>`` label."""
    dt_part, _, _ = store_id.partition("_observation_")
    when = dt_part
    if m := _STORE_RE.match(dt_part):
        when = f"{m.group(1)} {m.group(2)}:{m.group(3)}"
    obj = observation_object_name(store_id)  # cached catalog → friendly name (warmed at setup)
    return f"{obj} · {when}" if obj else when


def _entries(hass: HomeAssistant) -> list[VaonisConfigEntry]:
    return [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if getattr(e, "runtime_data", None) is not None
    ]


class VaonisMediaSource(MediaSource):
    """Browse the telescope's observations (one folder each) and their frames."""

    name = "Vaonis"

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise the media source."""
        super().__init__(DOMAIN)
        self.hass = hass

    def _proxy(self, entry_id: str, kind: str, ref: str) -> str:
        """URL of the HA proxy view that streams a frame's bytes (used for leaves + thumbnails)."""
        return f"/api/vaonis_media/{entry_id}/{kind}/{ref}"

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a leaf identifier to the proxy-view URL."""
        parts = item.identifier.split("|")
        if len(parts) != 3 or parts[1] not in ("http", "ftp"):
            raise Unresolvable(f"Cannot resolve {item.identifier}")
        entry_id, kind, ref = parts
        mime = "image/jpeg" if kind == "http" else _mime_for(base64.urlsafe_b64decode(ref).decode())
        return PlayMedia(self._proxy(entry_id, kind, ref), mime)

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Browse the tree (root -> telescope -> observation -> frames)."""
        if not item.identifier:
            return self._folder(
                None,
                "Vaonis",
                [self._folder(e.entry_id, e.title, []) for e in _entries(self.hass)],
            )

        parts = item.identifier.split("|")
        entry = next((e for e in _entries(self.hass) if e.entry_id == parts[0]), None)
        if entry is None:
            raise Unresolvable(f"Unknown telescope: {parts[0]}")
        client = entry.runtime_data.client

        if len(parts) == 1:  # telescope root: live frame (if any) + one folder per observation
            children: list[BrowseMediaSource] = []
            if client.current_observation() is not None and (cur := client.current_image()):
                ref = _b64(cur.static_url(client.ip))
                children.append(
                    self._image(
                        f"{entry.entry_id}|http|{ref}",
                        "● Live",
                        self._proxy(entry.entry_id, "http", ref),
                    )
                )
            caps = [e for e in await client.library(FTP_ROOT) if e.is_dir]
            for cap in sorted(caps, key=lambda e: e.name, reverse=True):  # storeId date-prefixed
                # Each folder gets ONE cover thumbnail, fetched lazily when HA renders it (the `cover`
                # proxy picks a representative frame) — so the tree is a single listing, not a slow
                # download per frame. Frames themselves are shown without thumbnails (click to view).
                children.append(
                    self._folder(
                        f"{entry.entry_id}|obs|{_b64(cap.path)}",
                        _observation_label(cap.name),
                        [],
                        thumbnail=self._proxy(entry.entry_id, "cover", _b64(cap.path)),
                    )
                )
            return self._folder(entry.entry_id, entry.title, children)

        if parts[1] == "obs":  # one observation: its frames (newest first), click to view
            store_path = base64.urlsafe_b64decode(parts[2]).decode()
            frames = await client.observation_frames(store_path)
            if len(frames) > _MAX_FRAMES:
                _LOGGER.debug(
                    "observation %s has %d frames; showing newest %d",
                    store_path,
                    len(frames),
                    _MAX_FRAMES,
                )
            children = [
                # No per-frame thumbnail (each would be a slow FTP download); click opens the frame,
                # which the proxy fetches once and caches.
                self._image(f"{entry.entry_id}|ftp|{_b64(fe.path)}", fe.name)
                for fe in frames[:_MAX_FRAMES]
            ]
            label = _observation_label(store_path.rstrip("/").split("/")[-1])
            return self._folder(item.identifier, label, children)

        raise Unresolvable(f"Cannot browse {item.identifier}")

    def _folder(
        self,
        identifier: str | None,
        title: str,
        children: list[BrowseMediaSource],
        thumbnail: str | None = None,
    ) -> BrowseMediaSource:
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=identifier,
            media_class=MediaClass.DIRECTORY,
            media_content_type="image/jpeg",
            title=title,
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.IMAGE,
            thumbnail=thumbnail,
        )

    def _image(
        self, identifier: str, title: str, thumbnail: str | None = None
    ) -> BrowseMediaSource:
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=identifier,
            media_class=MediaClass.IMAGE,
            media_content_type="image/jpeg",
            title=title,
            can_play=True,
            can_expand=False,
            thumbnail=thumbnail,
        )
