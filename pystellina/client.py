"""Async client for a Vaonis Stellina over its local Wi-Fi API.

Lifecycle::

    async with StellinaClient() as scope:
        await scope.take_control()
        await scope.park()

The client opens a Socket.IO v2 / Engine.IO v3 connection (port 8083) to receive the live
status stream (which carries the rotating auth ``challenge`` and arrives as ``STATUS_UPDATED``)
and to acquire control, and issues REST commands (port 8082) signed per-request from the latest
status.

**Safety:** the firmware-upload endpoint is never callable; irreversible (delete/reset) and
solar (``sun/*``) endpoints require an explicit opt-in; and state-changing commands are guarded
against being issued without control, while shutting down, or mid-operation. See PROTOCOL.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable
from collections.abc import Callable
from types import TracebackType
from typing import Any

import aiohttp

from . import astro
from . import const
from . import ftp
from ._eio3 import EngineIO3Client
from ._eio3 import StellinaSocketError
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
    """The telescope rejected a command or it was refused by a client-side safety guard."""


def _extract_status(args: list[Any]) -> dict[str, Any] | None:
    """Fallback: find a status dict (one containing ``challenge``) in an event payload.

    The primary path is the ``STATUS_UPDATED`` event; this shape-based detection is a safety net
    in case a firmware variant uses a different event name.
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
        # Stable per-machine id so reconnects reuse one entry instead of piling up in
        # connectedDevices (uuid.getnode() is the host's MAC-derived node id).
        self.device_id = device_id or f"pystellina-{uuid.getnode():x}"
        self.name = name
        self.country_code = country_code
        self.request_timeout = request_timeout
        self.status: StellinaStatus | None = None
        self.last_control_error: Any = None

        self._session = session
        self._owns_session = session is None
        self._first_status = asyncio.Event()
        self._status_callbacks: list[StatusCallback] = []
        self._sock = EngineIO3Client()
        self._sock.on(const.EVENT_STATUS, self._on_status)
        self._sock.on(const.EVENT_CONTROL_ERROR, self._on_control_error)
        self._sock.on_any(self._on_any)

    @property
    def connected(self) -> bool:
        """Whether the socket connection is currently open."""
        return self._sock.connected

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

    # -- socket ------------------------------------------------------------------------
    def on_status(self, callback: StatusCallback) -> None:
        """Register a callback invoked on every status push."""
        self._status_callbacks.append(callback)

    async def _on_status(self, payload: Any) -> None:
        raw = payload if isinstance(payload, dict) else _extract_status([payload])
        if raw is None:
            return
        await self._apply_status(raw)

    async def _on_control_error(self, payload: Any) -> None:
        self.last_control_error = payload
        _LOGGER.warning("Stellina CONTROL_ERROR: %s", payload)

    async def _on_any(self, event: str, *args: Any) -> None:
        if event == const.EVENT_STATUS:
            return  # handled by _on_status
        raw = _extract_status(list(args))
        if raw is not None:
            _LOGGER.debug("status recovered from event %s (not %s)", event, const.EVENT_STATUS)
            await self._apply_status(raw)

    async def _apply_status(self, raw: dict[str, Any]) -> None:
        status = StellinaStatus.model_validate(raw)
        self.status = status
        self._first_status.set()
        for callback in self._status_callbacks:
            result = callback(status)
            if asyncio.iscoroutine(result):
                await result

    async def connect(self, *, status_timeout: float = 15.0) -> StellinaStatus:
        """Open the socket connection and wait for the first status."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        self._sock._session = self._session  # share our session; we own its lifecycle
        self._sock._owns_session = False
        query = f"id={self.device_id}&name={self.name}&countryCode={self.country_code}"
        try:
            await self._sock.connect(self.ip, const.SOCKET_PORT, const.SOCKET_PATH, query)
        except (StellinaSocketError, aiohttp.ClientError, TimeoutError, OSError) as err:
            raise StellinaConnectionError(f"socket connect failed: {err}") from err
        try:
            await asyncio.wait_for(self._first_status.wait(), timeout=status_timeout)
        except TimeoutError as err:
            raise StellinaConnectionError(
                "connected but received no STATUS_UPDATED with a 'challenge' "
                "(check Engine.IO version / event name — run `stellina --debug watch`)"
            ) from err
        assert self.status is not None
        return self.status

    async def disconnect(self) -> None:
        """Release control if held, then disconnect and close our session."""
        try:
            if self.connected and self.has_control:
                await self.release_control()
        except Exception:
            _LOGGER.debug("error releasing control on disconnect", exc_info=True)
        try:
            await self._sock.disconnect()
        except Exception:
            _LOGGER.debug("error disconnecting socket", exc_info=True)
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def _emit(self, key: str, value: Any | None = None) -> None:
        if value is None:
            await self._sock.emit(const.SOCKET_EVENT, key)
        else:
            await self._sock.emit(const.SOCKET_EVENT, key, value)

    async def _wait_for_status(
        self, predicate: Callable[[StellinaStatus], bool], *, timeout: float, desc: str
    ) -> None:
        """Block until a pushed status satisfies ``predicate`` (or raise on timeout).

        Control and operation state only become true once the telescope echoes them back in the
        ``STATUS_UPDATED`` stream — the same signal the app waits on (``connectedAsMaster`` /
        ``currentOperation``). Polling our cached ``self.status`` synchronously races that echo.
        """
        event = asyncio.Event()

        def _check(status: StellinaStatus) -> None:
            if predicate(status):
                event.set()

        self._status_callbacks.append(_check)
        try:
            if self.status is not None and predicate(self.status):
                return
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except TimeoutError as err:
            raise StellinaCommandError(
                f"timed out after {timeout:.0f}s waiting for {desc}"
            ) from err
        finally:
            self._status_callbacks.remove(_check)

    async def take_control(self, *, wait: bool = True, timeout: float = 10.0) -> None:
        """Become the controlling ("master") device.

        Note: this forcibly takes control — if the owner's phone app currently holds it, it will
        be demoted. (The phone can take it back, after which our commands will start failing.)

        With ``wait`` (default), block until the status stream confirms ``masterDeviceId`` is us —
        otherwise the immediately-following command races the confirmation and fails its
        ``_require_control`` guard. This mirrors the app, which only enables commands once it sees
        ``connectedAsMaster``.
        """
        if self.has_control:
            return
        if self.status and self.status.master_device_id:
            _LOGGER.warning("taking control from current master %s", self.status.master_device_id)
        await self._emit(const.MSG_TAKE_CONTROL)
        await self._emit(const.MSG_SET_USER_NAME, {"device": self.device_id, "user": self.name})
        if wait:
            await self._wait_for_status(
                lambda s: s.master_device_id == self.device_id,
                timeout=timeout,
                desc="control confirmation (masterDeviceId)",
            )

    async def release_control(self) -> None:
        """Give up control."""
        await self._emit(const.MSG_RELEASE_CONTROL)

    @property
    def has_control(self) -> bool:
        """Whether this device currently holds control."""
        return bool(self.status and self.status.master_device_id == self.device_id)

    # -- safety / precondition guards ---------------------------------------------------
    def _require_control(self, action: str) -> None:
        if self.status is None:
            raise StellinaCommandError(f"{action}: not connected (no status yet)")
        if self.status.shutting_down:
            raise StellinaCommandError(f"{action}: telescope is shutting down")
        if not self.has_control:
            raise StellinaCommandError(
                f"{action}: this client does not hold control; take_control() first"
            )

    def _require_idle(self, action: str) -> None:
        self._require_control(action)
        assert self.status is not None
        busy = self.status.active_operation
        if busy:
            raise StellinaCommandError(
                f"{action}: an operation is already running ({busy}); stop it first"
            )

    async def _wait_idle(self, *, timeout: float = 30.0) -> None:
        """Block until no operation is running (``currentOperation`` cleared or stopped).

        A ``stopObservation`` POST returns immediately, but the firmware keeps ``currentOperation``
        in the status until it has actually wound the stack down — so an immediate
        ``startObservation`` still trips the busy guard. The app waits for the status to clear; so
        do we.
        """
        await self._wait_for_status(
            lambda s: not s.is_busy, timeout=timeout, desc="telescope to become idle"
        )

    @staticmethod
    def _guard_endpoint(endpoint: str, *, allow_unsafe: bool) -> None:
        base = endpoint.split("?", 1)[0].strip("/")
        if base == const.FIRMWARE_ENDPOINT:
            raise StellinaCommandError(
                f"refusing {base}: firmware upload is blocked (the only true brick vector)"
            )
        if not allow_unsafe and base in const.DESTRUCTIVE_ENDPOINTS:
            raise StellinaCommandError(
                f"refusing {base}: irreversible (data-loss/reset). Pass allow_unsafe=True to override."
            )
        if not allow_unsafe and base.startswith(const.SOLAR_ENDPOINT_PREFIX):
            raise StellinaCommandError(
                f"refusing {base}: solar mode requires the Vaonis solar filter — "
                "pass allow_unsafe=True only if it is installed (else the sensor can be destroyed)."
            )

    # -- REST ---------------------------------------------------------------------------
    def _auth_header(self) -> str:
        if self.status is None:
            raise StellinaCommandError("no status yet; call connect() first")
        return build_auth_header(*self.status.auth_args())

    async def request(
        self,
        method: str,
        endpoint: str,
        body: dict[str, Any] | None = None,
        *,
        allow_unsafe: bool = False,
    ) -> dict[str, Any]:
        """Low-level signed REST call. Returns the parsed JSON body. Enforces endpoint guards."""
        self._guard_endpoint(endpoint, allow_unsafe=allow_unsafe)
        if self._session is None:
            raise StellinaCommandError("client not connected")
        url = const.base_url(self.ip) + endpoint
        headers = {"Authorization": self._auth_header()}
        timeout = aiohttp.ClientTimeout(total=self.request_timeout)
        try:
            async with self._session.request(
                method,
                url,
                json=body if method.upper() != "GET" else None,
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
        self,
        method: str,
        endpoint: str,
        body: dict[str, Any] | None = None,
        *,
        allow_unsafe: bool = False,
    ) -> dict[str, Any]:
        """Signed REST call for debugging: returns ``{status, body}`` and never raises on HTTP.

        Still enforces the endpoint safety guards (firmware/destructive/solar).
        """
        self._guard_endpoint(endpoint, allow_unsafe=allow_unsafe)
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

    async def post(
        self, endpoint: str, body: dict[str, Any] | None = None, *, allow_unsafe: bool = False
    ) -> dict[str, Any]:
        """POST a JSON body to an endpoint (defaults to ``{}``)."""
        return await self.request(
            "POST", endpoint, body if body is not None else {}, allow_unsafe=allow_unsafe
        )

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
        if not (-90.0 <= latitude <= 90.0) or not (-180.0 <= longitude <= 180.0):
            raise StellinaCommandError(f"latitude/longitude out of range: {latitude},{longitude}")
        self._require_idle("start_autoinit")
        body = AutoInitBody(
            latitude=latitude,
            longitude=longitude,
            time=int(time.time() * 1000),
            skip_auto_focus=skip_auto_focus,
        )
        return await self.post(const.Endpoint.START_AUTOINIT, body.model_dump(by_alias=True))

    async def stop_autoinit(self) -> dict[str, Any]:
        return await self.post(const.Endpoint.STOP_AUTOINIT)

    async def start_observation(
        self,
        observation: ObservationBody,
        *,
        allow_solar: bool = False,
        replace: bool = False,
        stop_timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Slew to a target and start imaging.

        Refuses unless the scope is initialised and idle, and (without ``allow_solar``) refuses
        targets within ~10° of the Sun.

        The app refuses to start an observation while another operation is running. With
        ``replace=True`` we mirror "just switch targets": if an *observation* is already running we
        stop it and wait for the telescope to go idle (up to ``stop_timeout``) before starting the
        new one. A non-observation operation (auto-init, park, plan) is never auto-stopped.
        """
        self._require_control("start_observation")
        assert self.status is not None
        busy = self.status.active_operation
        if busy:
            obs = self.current_observation()
            if not (replace and obs is not None):
                hint = "stop it first" if obs is not None else "stop that operation first"
                raise StellinaCommandError(
                    f"start_observation: an operation is already running ({busy}); {hint}"
                    + ("" if obs is None else " or pass replace=True")
                )
            _LOGGER.info("replace=True: stopping running observation before starting new target")
            await self.stop_observation()
            await self._wait_idle(timeout=stop_timeout)
        if not self.status.initialized:
            raise StellinaCommandError(
                "start_observation: telescope not initialised; run autoinit first"
            )
        if not allow_solar and observation.ra is not None and observation.de is not None:
            sep = astro.separation_from_sun(observation.ra, observation.de)
            if sep < const.SOLAR_EXCLUSION_DEG:
                raise StellinaCommandError(
                    f"target is {sep:.1f}° from the Sun (<{const.SOLAR_EXCLUSION_DEG}°); refused. "
                    "Imaging near the Sun without the Vaonis solar filter destroys the sensor. "
                    "Pass allow_solar=True only if the filter is installed."
                )
        return await self.post(const.Endpoint.START_OBSERVATION, observation.to_payload())

    async def observe_object(
        self, object_id: str, *, allow_solar: bool = False, replace: bool = False
    ) -> dict[str, Any]:
        """Slew to a catalog object (by id or name) using its recommended settings.

        ``replace=True`` stops a running observation first (see :meth:`start_observation`).
        """
        from .catalog import get_object

        obj = get_object(object_id)
        if obj is None:
            raise StellinaCommandError(f"unknown catalog object: {object_id!r}")
        try:
            observation = obj.to_observation()  # resolves ephemeris for solar objects
        except RuntimeError as err:  # ephem not installed for a solar object
            raise StellinaCommandError(str(err)) from err
        return await self.start_observation(observation, allow_solar=allow_solar, replace=replace)

    async def stop_observation(self) -> dict[str, Any]:
        return await self.post(const.Endpoint.STOP_OBSERVATION)

    # -- native plan (Plan My Night) ----------------------------------------------------
    async def start_plan(
        self,
        items: list[Any],
        *,
        name: str = "pystellina plan",
        latitude: float,
        longitude: float,
        start_time: Any | None = None,
        allow_solar: bool = False,
    ) -> dict[str, Any]:
        """Upload and start the telescope's native autonomous plan (``planner/startPlan``).

        ``items`` is a list of :class:`~pystellina.plan.PlanItem` (``target``/``minutes``). The
        firmware runs the whole plan itself — auto-initialising and advancing target-to-target on
        the schedule — so it keeps going after this client disconnects. Refused unless idle (the
        app gates startPlan on ``currentOperation == null``); stop any observation first.
        """
        from .plan import build_plan

        self._require_idle("start_plan")
        body = build_plan(
            list(items),
            name=name,
            latitude=latitude,
            longitude=longitude,
            device_id=self.device_id,
            start_time=start_time,
            observatory_name=(
                (self.status.raw.get("settings") or {}).get("telescopeName")
                if self.status
                else None
            )
            or "pystellina",
            allow_solar=allow_solar,
        )
        return await self.post(const.Endpoint.START_PLAN, body.to_payload())

    async def stop_plan(self) -> dict[str, Any]:
        """Cancel the running native plan (``planner/stopPlan``)."""
        self._require_control("stop_plan")
        return await self.post(const.Endpoint.STOP_PLAN)

    def plan_progress(self) -> Any:
        """Parsed progress of the running native plan, or None when no plan is active."""
        from .plan import PlanProgress

        if self.status is None:
            return None
        return PlanProgress.from_status(self.status.raw)

    # -- in-observation controls (the app's Change Framing / Restart autofocus / etc.) ---
    async def adjust_framing(self, x: int, y: int, rot: float = 0.0) -> dict[str, Any]:
        """Nudge the live framing — ``x``/``y`` integer offsets, ``rot`` in degrees."""
        self._require_control("adjust_framing")
        if self.current_observation() is None:
            raise StellinaCommandError("adjust_framing: no observation in progress")
        return await self.post(const.Endpoint.ADJUST_FRAMING, {"x": x, "y": y, "rot": rot})

    async def restart_autofocus(self, *, restart_capture: bool = True) -> dict[str, Any]:
        """Re-run deep-sky autofocus. ``restart_capture`` also restarts the current stack."""
        self._require_control("restart_autofocus")
        return await self.post(const.Endpoint.ADJUST_FOCUS, {"restartCapture": restart_capture})

    async def set_camera_params(
        self,
        *,
        gain: int | None = None,
        exposure_micro_sec: int | None = None,
        saturation: float | None = None,
    ) -> dict[str, Any]:
        """Live-tune camera parameters during an observation (only set fields are sent)."""
        self._require_control("set_camera_params")
        body: dict[str, Any] = {}
        if gain is not None:
            body["gain"] = gain
        if exposure_micro_sec is not None:
            body["exposureMicroSec"] = exposure_micro_sec
        if saturation is not None:
            body["saturation"] = saturation
        if not body:
            raise StellinaCommandError("set_camera_params: nothing to change")
        return await self.post(const.Endpoint.SET_USER_PARAMS, body)

    async def set_multi_light(self, enabled: bool) -> dict[str, Any]:
        """Toggle Multi-Light (CovalENS / HDR background; firmware >= 2.28).

        Merges the current settings so other settings aren't reset.
        """
        self._require_control("set_multi_light")
        current = (self.status.raw.get("settings") or {}) if self.status else {}
        # Echo back all known settings (setSettings may replace, not merge) + the toggle.
        keep = ("telescopeName", "storageFileCategories", "usbFileTypes", "enableLiveFocus",
                "enableFullResolution", "enableDithering", "enableDarkUsage", "algoHdrBackground",
                "buttonBrightness")  # fmt: skip
        body = {k: current[k] for k in keep if k in current}
        body["enableHdrBackground"] = enabled
        return await self.post(const.Endpoint.SET_SETTINGS, body)

    async def enable_multi_night(self) -> dict[str, Any]:
        """Enable "multi-night": keep the current stack in the telescope's stored-captures library
        so it can resume integrating on a later night (``capture/setToBeResumable``).

        NB: the app's "Save to phone/Singularity" buttons are image download/cloud-upload (app-side),
        not this telescope flag.
        """
        self._require_control("enable_multi_night")
        return await self.post(const.Endpoint.SET_TO_BE_RESUMABLE)

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
            # POST with captureId as a query param and no body (StellinaAPI.getCaptureJXL).
            resp = await self.request(
                "POST", f"{const.Endpoint.EXPORT_JPEGXL}?captureId={capture_id}"
            )
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
        """Return the arm to its parked position (refused mid-operation; stop first)."""
        self._require_idle("park")
        return await self.post(const.Endpoint.PARK)

    async def request_shutdown(self, *, force: bool = False) -> dict[str, Any]:
        """Power off the telescope board (drops the link; needs the physical button to restart).

        Refused while an operation is running unless ``force=True``.
        """
        self._require_control("request_shutdown")
        assert self.status is not None
        if self.status.is_busy and not force:
            raise StellinaCommandError(
                f"request_shutdown: operation running ({self.status.active_operation}); "
                "stop it first or pass force=True"
            )
        _LOGGER.warning("requesting shutdown — the telescope will power off and drop the link")
        return await self.post(const.Endpoint.REQUEST_SHUTDOWN)

    async def switch_frequency(self, band: str, *, force: bool = False) -> dict[str, Any]:
        """Switch the access-point band. **This drops your connection** — you must rejoin.

        ``band`` must be ``const.BAND_2_4_GHZ`` or ``const.BAND_5_GHZ``. Refused mid-operation
        unless ``force=True``.
        """
        if band not in (const.BAND_2_4_GHZ, const.BAND_5_GHZ):
            raise StellinaCommandError(f"invalid band {band!r} (use BAND_2_4_GHZ or BAND_5_GHZ)")
        self._require_control("switch_frequency")
        assert self.status is not None
        if self.status.is_busy and not force:
            raise StellinaCommandError(
                f"switch_frequency: operation running ({self.status.active_operation}); pass force=True"
            )
        _LOGGER.warning(
            "switching Wi-Fi band to %s — the connection will drop; rejoin to continue", band
        )
        return await self.post(const.Endpoint.SWITCH_FREQUENCY, {"band": band})
