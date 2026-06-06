"""Media source: browse recent captures and the saved FTP library in the HA media browser.

Identifiers are pipe-delimited: ``<entry_id>``, ``<entry_id>|recent``, ``<entry_id>|lib|<b64 path>``
for folders, and ``<entry_id>|http|<b64 url>`` / ``<entry_id>|ftp|<b64 path>`` for leaves.
Leaves resolve to the proxy view in :mod:`.http`, which streams the bytes through Home Assistant.
"""

from __future__ import annotations

import base64

from homeassistant.components.media_player import MediaClass
from homeassistant.components.media_source import BrowseMediaSource
from homeassistant.components.media_source import MediaSource
from homeassistant.components.media_source import MediaSourceItem
from homeassistant.components.media_source import PlayMedia
from homeassistant.components.media_source import Unresolvable
from homeassistant.core import HomeAssistant

# FTP_ROOT = "/system/captures" — where finished runs live (the device's /user is empty)
from pyvaonis.const import FTP_ROOT

from .const import DOMAIN
from .coordinator import VaonisConfigEntry
from .http import _mime_for


async def async_get_media_source(hass: HomeAssistant) -> VaonisMediaSource:
    """Set up the Vaonis media source."""
    return VaonisMediaSource(hass)


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _entries(hass: HomeAssistant) -> list[VaonisConfigEntry]:
    return [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if getattr(e, "runtime_data", None) is not None
    ]


class VaonisMediaSource(MediaSource):
    """Browse telescope captures: live/recent frames and the saved FTP archive."""

    name = "Vaonis"

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise the media source."""
        super().__init__(DOMAIN)
        self.hass = hass

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a leaf identifier to the proxy-view URL."""
        parts = item.identifier.split("|")
        if len(parts) != 3 or parts[1] not in ("http", "ftp"):
            raise Unresolvable(f"Cannot resolve {item.identifier}")
        entry_id, kind, ref = parts
        url = f"/api/vaonis_media/{entry_id}/{kind}/{ref}"
        mime = "image/jpeg" if kind == "http" else _mime_for(base64.urlsafe_b64decode(ref).decode())
        return PlayMedia(url, mime)

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Browse the tree (root -> telescope -> recent | library -> files)."""
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

        if len(parts) == 1:  # telescope root: two folders
            return self._folder(
                entry.entry_id,
                entry.title,
                [
                    self._folder(f"{entry.entry_id}|recent", "Recent captures", []),
                    self._folder(f"{entry.entry_id}|lib|{_b64(FTP_ROOT)}", "Saved library", []),
                ],
            )

        if parts[1] == "recent":
            # The newest finished run's frames from the FTP gallery (so it's useful while idle),
            # with the live frame on top when an observation is running. Newest frames first, capped.
            children = []
            if client.current_observation() is not None and (cur := client.current_image()):
                children.append(
                    self._image(f"{entry.entry_id}|http|{_b64(cur.static_url(client.ip))}", "live")
                )
            caps = [e for e in await client.library(FTP_ROOT) if e.is_dir]
            if caps:
                newest = max(caps, key=lambda e: e.name)  # storeId is date-prefixed
                frames = [
                    e
                    for e in await client.library(f"{newest.path}/images")
                    if not e.is_dir and e.name.lower().endswith((".jpg", ".jpeg"))
                ]
                for fe in sorted(frames, key=lambda e: e.name, reverse=True)[:60]:
                    children.append(self._image(f"{entry.entry_id}|ftp|{_b64(fe.path)}", fe.name))
            return self._folder(item.identifier, "Recent captures", children)

        if parts[1] == "lib":
            path = base64.urlsafe_b64decode(parts[2]).decode()
            children: list[BrowseMediaSource] = []
            for fe in await client.library(path):
                if fe.is_dir:
                    children.append(
                        self._folder(f"{entry.entry_id}|lib|{_b64(fe.path)}", fe.name, [])
                    )
                else:
                    children.append(self._image(f"{entry.entry_id}|ftp|{_b64(fe.path)}", fe.name))
            return self._folder(item.identifier, path, children)

        raise Unresolvable(f"Cannot browse {item.identifier}")

    def _folder(
        self, identifier: str | None, title: str, children: list[BrowseMediaSource]
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
        )

    def _image(self, identifier: str, title: str) -> BrowseMediaSource:
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=identifier,
            media_class=MediaClass.IMAGE,
            media_content_type="image/jpeg",
            title=title,
            can_play=True,
            can_expand=False,
        )
