"""Async client for a Vaonis Stellina over its local Wi-Fi API.

Lifecycle::

    async with StellinaClient() as scope:
        await scope.take_control()
        await scope.park()

The client opens a socket.io connection (port 8083) to receive the live status
stream (which carries the rotating auth ``challenge``) and to acquire control, and
issues REST commands (port 8082) signed per-request from the latest status.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable
from collections.abc import Callable
from types import TracebackType
from typing import Any

import aiohttp
import socketio
from socketio.exceptions import ConnectionError as SioConnectionError

from . import const
from . import ftp
from .auth import build_auth_header
from .ftp import FtpEntry
from .models import AutoInitBody
from .models import ObservationBody
from .models import StellinaStatus
from .observation import LiveImage
from .observation import ObservationProgress
from .observation import recent_images

_LOGGER = logging.getLogger(__name__)

StatusCallback = Callable[[StellinaStatus], Awaitable[None] | None]


class StellinaError(Exception):
    """Base error."""


class StellinaConnectionError(StellinaError):
    """Network/transport error talking to the telescope."""


class StellinaCommandError(StellinaError):
    """The telescope rejected a command or returned a non-success result."""


def _extract_status(args: list[Any]) -> dict[str, Any] | None:
    """Find a status dict (one containing ``challenge``) in a socket.io payload.

    The exact inbound event name is set in the one method that did not decompile, so
    we accept status from any event by shape rather than by name (see PROTOCOL.md §2).
    """
    for item in args:
        if isinstance(item, str):
            try:
                item = json.loads(item)
            except (ValueError, TypeError):
                continue
        if isinstance(item, dict):
            if "challenge" in item:
                return item
            for value in item.values():
                if isinstance(value, dict) and "challenge" in value:
                    return value
    return None


class StellinaClient:
    """Talk to a Stellina on its access-point network."""

    def __init__(
        self,
        ip: str = const.DEFAULT_IP,
        *,
        device_id: str | None = None,
        name: str = "pystellina",
        country_code: str = "US",
        session: aiohttp.ClientSession | None = None,
        request_timeout: float = 20.0,
    ) -> None:
        self.ip = ip
        self.device_id = device_id or f"pystellina-{uuid.uuid4()}"
        self.name = name
        self.country_code = country_code
        self.request_timeout = request_timeout
        self.status: StellinaStatus | None = None

        self._session = session
        self._owns_session = session is None
        self._sio = socketio.AsyncClient(reconnection=True)
        self._first_status = asyncio.Event()
        self._status_callbacks: list[StatusCallback] = []
        self._sio.on("*", self._on_any_event)

    @property
    def connected(self) -> bool:
        """Whether the socket.io connection is currently open."""
        return self._sio.connected

    # -- context management -------------------------------------------------------------
    async def __aenter__(self) -> StellinaClient:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.disconnect()

    # -- socket.io ----------------------------------------------------------------------
    def on_status(self, callback: StatusCallback) -> None:
        """Register a callback invoked on every status push."""
        self._status_callbacks.append(callback)

    async def _on_any_event(self, event: str, *args: Any) -> None:
        raw = _extract_status(list(args))
        if raw is None:
            _LOGGER.debug("socket.io event %s carried no status", event)
            return
        status = StellinaStatus.model_validate(raw)
        self.status = status
        self._first_status.set()
        for callback in self._status_callbacks:
            result = callback(status)
            if asyncio.iscoroutine(result):
                await result

    async def connect(self, *, status_timeout: float = 15.0) -> StellinaStatus:
        """Open the socket.io connection and wait for the first status."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        query = f"id={self.device_id}&name={self.name}&countryCode={self.country_code}"
        url = f"{const.socket_url(self.ip)}/?{query}"
        try:
            # transports=["websocket"] skips the polling handshake; drop if it fails.
            await self._sio.connect(url, socketio_path=const.SOCKET_PATH, transports=["websocket"])
        except SioConnectionError as err:
            raise StellinaConnectionError(f"socket.io connect failed: {err}") from err
        try:
            await asyncio.wait_for(self._first_status.wait(), timeout=status_timeout)
        except TimeoutError as err:
            raise StellinaConnectionError(
                "connected but received no status with a 'challenge' "
                "(check socket.io version / event name, see PROTOCOL.md)"
            ) from err
        assert self.status is not None
        return self.status

    async def disconnect(self) -> None:
        """Disconnect socket.io and close the session if we created it."""
        try:
            await self._sio.disconnect()
        except Exception:
            _LOGGER.debug("error disconnecting socket.io", exc_info=True)
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def _emit(self, key: str, value: Any | None = None) -> None:
        if value is None:
            await self._sio.emit(const.SOCKET_EVENT, key)
        else:
            await self._sio.emit(const.SOCKET_EVENT, key, value)

    async def take_control(self) -> None:
        """Become the controlling ("master") device."""
        await self._emit(const.MSG_TAKE_CONTROL)
        await self._emit(const.MSG_SET_USER_NAME, {"device": self.device_id, "user": self.name})

    async def release_control(self) -> None:
        """Give up control."""
        await self._emit(const.MSG_RELEASE_CONTROL)

    @property
    def has_control(self) -> bool:
        """Whether this device currently holds control."""
        return bool(self.status and self.status.master_device_id == self.device_id)

    # -- REST ---------------------------------------------------------------------------
    def _auth_header(self) -> str:
        if self.status is None:
            raise StellinaCommandError("no status yet; call connect() first")
        return build_auth_header(*self.status.auth_args())

    async def request(
        self, method: str, endpoint: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Low-level signed REST call. Returns the parsed JSON body."""
        if self._session is None:
            raise StellinaCommandError("client not connected")
        url = const.base_url(self.ip) + endpoint
        headers = {"Authorization": self._auth_header()}
        timeout = aiohttp.ClientTimeout(total=self.request_timeout)
        try:
            async with self._session.request(
                method,
                url,
                json=body if method == "POST" else None,
                headers=headers,
                timeout=timeout,
            ) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise StellinaCommandError(f"{endpoint} -> HTTP {resp.status}: {text}")
                try:
                    data: dict[str, Any] = await resp.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    return {"raw": text}
                if data.get("success") is False:
                    raise StellinaCommandError(f"{endpoint} returned success=false")
                return data
        except aiohttp.ClientError as err:
            raise StellinaConnectionError(f"{endpoint}: {err}") from err

    async def call(
        self, method: str, endpoint: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Signed REST call for debugging: returns ``{status, body}`` and never raises on HTTP.

        Unlike :meth:`request`, this surfaces the HTTP status and raw body even on errors —
        the building block for the ``stellina api`` command.
        """
        if self._session is None:
            raise StellinaCommandError("client not connected")
        url = const.base_url(self.ip) + endpoint
        headers = {"Authorization": self._auth_header()}
        timeout = aiohttp.ClientTimeout(total=self.request_timeout)
        send = body if method.upper() != "GET" else None
        async with self._session.request(
            method, url, json=send, headers=headers, timeout=timeout
        ) as resp:
            text = await resp.text()
            try:
                parsed: Any = json.loads(text)
            except ValueError:
                parsed = text
            return {"status": resp.status, "body": parsed}

    async def post(self, endpoint: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """POST a JSON body to an endpoint (defaults to ``{}``)."""
        return await self.request("POST", endpoint, body if body is not None else {})

    async def get(self, endpoint: str) -> dict[str, Any]:
        """GET an endpoint."""
        return await self.request("GET", endpoint)

    # -- convenience commands -----------------------------------------------------------
    async def app_status(self) -> dict[str, Any]:
        """GET ``app/status``."""
        return await self.get(const.Endpoint.APP_STATUS)

    async def start_autoinit(
        self, latitude: float, longitude: float, *, skip_auto_focus: bool = False
    ) -> dict[str, Any]:
        """Initialise/align the telescope at a location."""
        import time

        body = AutoInitBody(
            latitude=latitude,
            longitude=longitude,
            time=int(time.time() * 1000),
            skip_auto_focus=skip_auto_focus,
        )
        return await self.post(const.Endpoint.START_AUTOINIT, body.model_dump(by_alias=True))

    async def stop_autoinit(self) -> dict[str, Any]:
        return await self.post(const.Endpoint.STOP_AUTOINIT)

    async def start_observation(self, observation: ObservationBody) -> dict[str, Any]:
        """Slew to a target and start imaging."""
        return await self.post(const.Endpoint.START_OBSERVATION, observation.to_payload())

    async def observe_object(self, object_id: str) -> dict[str, Any]:
        """Slew to a catalog object (by id or name) using its recommended settings."""
        from .catalog import get_object

        obj = get_object(object_id)
        if obj is None:
            raise StellinaCommandError(f"unknown catalog object: {object_id!r}")
        try:
            observation = obj.to_observation()  # resolves ephemeris for solar objects
        except RuntimeError as err:  # ephem not installed for a solar object
            raise StellinaCommandError(str(err)) from err
        return await self.start_observation(observation)

    async def stop_observation(self) -> dict[str, Any]:
        return await self.post(const.Endpoint.STOP_OBSERVATION)

    # -- live observation / images ------------------------------------------------------
    def current_observation(self) -> ObservationProgress | None:
        """Parsed progress of the current observation (target, step, stacking), or None."""
        if self.status is None:
            return None
        return ObservationProgress.from_status(self.status.raw)

    def current_image(self) -> LiveImage | None:
        """The latest live-stacked frame descriptor, or None when idle."""
        obs = self.current_observation()
        return obs.live_image if obs else None

    def recent_images(self) -> list[LiveImage]:
        """Latest frame plus recent finished captures (newest first)."""
        if self.status is None:
            return []
        return recent_images(self.status.raw)

    async def fetch_image(self, image: LiveImage | str) -> bytes:
        """Download an image (live frame or full URL). No auth required."""
        if self._session is None:
            raise StellinaCommandError("client not connected")
        url = image.url(self.ip) if isinstance(image, LiveImage) else image
        timeout = aiohttp.ClientTimeout(total=self.request_timeout)
        try:
            async with self._session.get(url, timeout=timeout) as resp:
                if resp.status >= 400:
                    raise StellinaCommandError(f"image {url} -> HTTP {resp.status}")
                return await resp.read()
        except aiohttp.ClientError as err:
            raise StellinaConnectionError(f"image fetch failed: {err}") from err

    async def fetch_current_image(self) -> bytes | None:
        """Download the current live-stacked frame, or None when idle."""
        image = self.current_image()
        return await self.fetch_image(image) if image else None

    # -- full-resolution export & saved library -----------------------------------------
    async def export_url(self, capture_id: str, fmt: str = "tiff") -> str:
        """Ask the telescope to render a full-res export and return its download URL.

        ``fmt`` is ``"tiff"`` (``capture/exportImageTiff``) or ``"jxl"`` (JPEG-XL,
        ``capture/exportImageJpegXl``). Rendering can take a while; the call waits for it.
        """
        if fmt == "tiff":
            resp = await self.request("POST", const.Endpoint.EXPORT_TIFF, {"captureId": capture_id})
        elif fmt == "jxl":
            resp = await self.get(f"{const.Endpoint.EXPORT_JPEGXL}?captureId={capture_id}")
        else:
            raise StellinaCommandError(f"unknown export format: {fmt!r} (use 'tiff' or 'jxl')")
        url = (resp.get("result") or {}).get("url")
        if not url:
            raise StellinaCommandError(f"export of {capture_id} returned no url: {resp}")
        return const.http_url(self.ip, url)

    async def export_capture(self, capture_id: str, fmt: str = "tiff") -> bytes:
        """Render and download a full-resolution capture (TIFF or JPEG-XL)."""
        return await self.fetch_image(await self.export_url(capture_id, fmt))

    async def library(self, path: str = const.FTP_ROOT) -> list[FtpEntry]:
        """List the saved-image library over FTP (defaults to ``/user``)."""
        return await ftp.list_dir(path, ip=self.ip)

    async def download_file(self, path: str) -> bytes:
        """Download a saved file from the library by its FTP path."""
        return await ftp.download(path, ip=self.ip)

    async def park(self) -> dict[str, Any]:
        return await self.post(const.Endpoint.PARK)

    async def request_shutdown(self) -> dict[str, Any]:
        return await self.post(const.Endpoint.REQUEST_SHUTDOWN)

    async def switch_frequency(self, band: str) -> dict[str, Any]:
        """Switch the access point band (``const.BAND_2_4_GHZ`` / ``const.BAND_5_GHZ``)."""
        return await self.post(const.Endpoint.SWITCH_FREQUENCY, {"band": band})
