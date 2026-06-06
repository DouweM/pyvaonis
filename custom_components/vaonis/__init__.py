"""The Stellina integration."""

from __future__ import annotations

import functools
import logging
import os
from contextlib import suppress
from pathlib import Path

import voluptuous as vol
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.core import ServiceCall
from homeassistant.core import ServiceResponse
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .coordinator import VaonisConfigEntry
from .coordinator import VaonisCoordinator
from .http import register_view
from .pyvaonis import VaonisError

_LOGGER = logging.getLogger(__name__)


def _guard_service(handler):  # type: ignore[no-untyped-def]
    """Wrap a service handler so telescope errors surface as a clean HomeAssistantError."""

    @functools.wraps(handler)
    async def wrapper(call: ServiceCall):  # type: ignore[no-untyped-def]
        try:
            return await handler(call)
        except VaonisError as err:
            raise HomeAssistantError(str(err)) from err

    return wrapper


PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.IMAGE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

SERVICE_EXPORT_CAPTURE = "export_capture"
SERVICE_RUN_PLAN = "run_plan"
SERVICE_STOP_PLAN = "stop_plan"
SERVICE_OBSERVE = "observe"
SERVICE_AUTOINIT = "autoinit"
SERVICE_ADJUST_FRAMING = "adjust_framing"
SERVICE_SET_CAMERA_PARAMS = "set_camera_params"
SERVICE_RESUME = "resume"
SERVICE_DELETE_CAPTURE = "delete_capture"
OBSERVE_SCHEMA = vol.Schema(
    {
        vol.Required("target"): str,
        vol.Optional("allow_solar", default=False): bool,
        # Advanced observation (deep-sky only): a mosaic field bigger than one frame (both degrees),
        # and/or saving the capture for multi-night resume.
        vol.Optional("mosaic_width"): vol.Coerce(float),
        vol.Optional("mosaic_height"): vol.Coerce(float),
        vol.Optional("multi_night", default=False): bool,
    }
)
RESUME_SCHEMA = vol.Schema({vol.Optional("store_id"): str})
DELETE_CAPTURE_SCHEMA = vol.Schema({vol.Required("store_id"): str})
AUTOINIT_SCHEMA = vol.Schema(
    {
        vol.Optional("latitude"): vol.Coerce(float),
        vol.Optional("longitude"): vol.Coerce(float),
        vol.Optional("skip_autofocus", default=False): bool,
    }
)
ADJUST_FRAMING_SCHEMA = vol.Schema(
    {
        vol.Optional("x", default=0): vol.Coerce(int),
        vol.Optional("y", default=0): vol.Coerce(int),
        vol.Optional("rot", default=0.0): vol.Coerce(float),
    }
)
SET_CAMERA_PARAMS_SCHEMA = vol.Schema(
    {
        vol.Optional("gain"): vol.Coerce(int),
        vol.Optional("exposure_seconds"): vol.Coerce(float),
        vol.Optional("saturation"): vol.Coerce(float),
    }
)
EXPORT_SCHEMA = vol.Schema(
    {
        vol.Optional("capture_id"): str,
        vol.Optional("format", default="tiff"): vol.In(["tiff", "jxl"]),
    }
)
RUN_PLAN_SCHEMA = vol.Schema(
    {
        vol.Required("targets"): [str],  # e.g. ["M42:30", "Andromeda Galaxy:45", "Jupiter:10"]
        # The native plan runs autonomously (auto-init → targets → park). It has no power-off step;
        # dark-gating is handled by scheduling the start, hence only wait_for_dark here.
        vol.Optional("wait_for_dark", default=True): bool,
    }
)
# Weather/dew/rain gating is intentionally NOT done here — gate the automation that calls this
# on your own HA entities (e.g. weather.microclimate condition + humidity), which are better
# calibrated for your site than any generic forecast. `stellina forecast` (CLI) keeps a
# standalone Open-Meteo check for off-grid use without Home Assistant.


async def async_setup_entry(hass: HomeAssistant, entry: VaonisConfigEntry) -> bool:
    """Set up Stellina from a config entry."""
    coordinator = VaonisCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    # Name the entry after the telescope itself (the app's telescopeName, e.g. "Stellina"), so the
    # integration + media browser show that rather than the generic default.
    status = coordinator.data
    if status is not None:
        from .pyvaonis import model_display_name

        name = (status.raw.get("settings") or {}).get("telescopeName") or model_display_name(
            status.model
        )
        if name and entry.title != name:
            hass.config_entries.async_update_entry(entry, title=name)

    # Warm the (cached) bundled catalog off the event loop so entity setup / the select don't do a
    # blocking ~0.5 MB JSON read on the loop. After this, load_catalog() is served from cache.
    from .pyvaonis import load_catalog

    await hass.async_add_executor_job(load_catalog)

    entry.runtime_data = coordinator
    register_view(hass)
    _register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: VaonisConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_EXPORT_CAPTURE):
        return

    async def export_capture(call: ServiceCall) -> ServiceResponse:
        """Render a full-res capture and save it under the HA media directory."""
        entries = [
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if getattr(e, "runtime_data", None) is not None
        ]
        if not entries:
            raise HomeAssistantError("No Stellina telescope is set up")
        client = entries[0].runtime_data.client

        capture_id = call.data.get("capture_id")
        if not capture_id:
            obs = client.current_observation()
            capture_id = obs.live_image.capture_id if obs and obs.live_image else None
        if not capture_id:
            raise HomeAssistantError("No capture_id given and nothing is being observed")

        fmt: str = call.data["format"]
        # The full-res render blocks server-side for minutes; raise the (shared client) timeout
        # for the duration so it doesn't trip the 20s default, then restore it.
        prev_timeout = client.request_timeout
        client.request_timeout = 300.0
        try:
            data = await client.export_capture(capture_id, fmt)
        finally:
            client.request_timeout = prev_timeout

        media_root = hass.config.media_dirs.get("local") or hass.config.path("media")
        out_dir = Path(media_root) / "vaonis"
        await hass.async_add_executor_job(functools.partial(os.makedirs, out_dir, exist_ok=True))
        out_path = out_dir / f"{capture_id}.{'tif' if fmt == 'tiff' else 'jxl'}"
        await hass.async_add_executor_job(out_path.write_bytes, data)
        return {"file": str(out_path), "bytes": len(data)}

    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT_CAPTURE,
        _guard_service(export_capture),
        schema=EXPORT_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    def _first_coordinator() -> VaonisCoordinator:
        entries = [
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if getattr(e, "runtime_data", None) is not None
        ]
        if not entries:
            raise HomeAssistantError("No Stellina telescope is set up")
        return entries[0].runtime_data

    def _resolve_location(coordinator: VaonisCoordinator, call: ServiceCall) -> tuple[float, float]:
        """Where the scope is: explicit service fields > the telescope's own position > HA's home.

        The scope (no GPS) only reports a position once it's been initialised before, so HA's
        configured home location is the fallback for a first init.
        """
        scope = coordinator.client.location()
        lat = call.data.get("latitude")
        lon = call.data.get("longitude")
        if lat is None:
            lat = scope[0] if scope else hass.config.latitude
        if lon is None:
            lon = scope[1] if scope else hass.config.longitude
        return lat, lon

    async def run_plan(call: ServiceCall) -> ServiceResponse:
        """Start the telescope's native autonomous plan (gate weather in the automation)."""
        from datetime import UTC
        from datetime import datetime

        from .pyvaonis import PlanItem
        from .pyvaonis import observing_window

        coordinator = _first_coordinator()
        lat, lon = _resolve_location(coordinator, call)
        items = [PlanItem.parse(t) for t in call.data["targets"]]

        start_time = None
        if call.data["wait_for_dark"]:
            window = observing_window(lat, lon)
            if window is not None:
                start_time = max(window[0], datetime.now(UTC))

        await coordinator.run_action(
            lambda c: c.start_plan(
                items,
                name="Home Assistant plan",
                latitude=lat,
                longitude=lon,
                start_time=start_time,
            )
        )
        return {"started": True, "targets": call.data["targets"]}

    async def stop_plan(call: ServiceCall) -> None:
        """Cancel the running native plan."""
        with suppress(Exception):
            await _first_coordinator().run_action(lambda c: c.stop_plan())

    hass.services.async_register(
        DOMAIN,
        SERVICE_RUN_PLAN,
        _guard_service(run_plan),
        schema=RUN_PLAN_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(DOMAIN, SERVICE_STOP_PLAN, _guard_service(stop_plan))

    async def observe(call: ServiceCall) -> None:
        """Slew to a catalog object (by id/name/designation) and start imaging.

        Optional mosaic (deep-sky only): pass both ``mosaic_width`` and ``mosaic_height`` (degrees).
        ``multi_night`` saves the capture for later resume.
        """
        mw, mh = call.data.get("mosaic_width"), call.data.get("mosaic_height")
        if (mw is None) != (mh is None):
            raise HomeAssistantError("mosaic needs both mosaic_width and mosaic_height (degrees)")
        mosaic = (mw, mh) if mw is not None and mh is not None else None
        await _first_coordinator().run_action(
            lambda c: c.observe_object(
                call.data["target"],
                allow_solar=call.data["allow_solar"],
                replace=True,
                mosaic=mosaic,
                multi_night=call.data["multi_night"],
            )
        )

    hass.services.async_register(
        DOMAIN, SERVICE_OBSERVE, _guard_service(observe), schema=OBSERVE_SCHEMA
    )

    async def resume(call: ServiceCall) -> None:
        """Resume a saved multi-night capture (newest if none given), continuing its stack."""
        coordinator = _first_coordinator()
        store_id = call.data.get("store_id")
        if not store_id:
            saved = coordinator.client.stored_captures()
            if len(saved) != 1:
                raise HomeAssistantError(
                    f"specify store_id ({len(saved)} saved captures)"
                    if saved
                    else "no saved multi-night captures to resume"
                )
            store_id = saved[0].get("storeId")
        await coordinator.run_action(lambda c: c.resume_capture(store_id, replace=True))

    hass.services.async_register(
        DOMAIN, SERVICE_RESUME, _guard_service(resume), schema=RESUME_SCHEMA
    )

    async def delete_capture(call: ServiceCall) -> None:
        """Delete a saved multi-night capture (irreversible)."""
        await _first_coordinator().run_action(
            lambda c: c.delete_stored_capture(call.data["store_id"], allow_unsafe=True)
        )

    hass.services.async_register(
        DOMAIN, SERVICE_DELETE_CAPTURE, _guard_service(delete_capture), schema=DELETE_CAPTURE_SCHEMA
    )

    async def autoinit(call: ServiceCall) -> None:
        """Initialise/align the telescope.

        Location defaults to the telescope's own last-known position, then HA's configured home.
        """
        coordinator = _first_coordinator()
        lat, lon = _resolve_location(coordinator, call)
        await coordinator.run_action(
            lambda c: c.start_autoinit(lat, lon, skip_auto_focus=call.data["skip_autofocus"])
        )

    async def adjust_framing(call: ServiceCall) -> None:
        """Nudge the live framing (the app's 'Change Framing'); safe during an observation."""
        await _first_coordinator().run_action(
            lambda c: c.adjust_framing(call.data["x"], call.data["y"], call.data["rot"])
        )

    async def set_camera_params(call: ServiceCall) -> None:
        """Live-tune gain/exposure/saturation during an observation."""
        exposure_us = call.data.get("exposure_seconds")
        await _first_coordinator().run_action(
            lambda c: c.set_camera_params(
                gain=call.data.get("gain"),
                exposure_micro_sec=(
                    int(exposure_us * 1_000_000) if exposure_us is not None else None
                ),
                saturation=call.data.get("saturation"),
            )
        )

    hass.services.async_register(
        DOMAIN, SERVICE_AUTOINIT, _guard_service(autoinit), schema=AUTOINIT_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ADJUST_FRAMING, _guard_service(adjust_framing), schema=ADJUST_FRAMING_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_CAMERA_PARAMS,
        _guard_service(set_camera_params),
        schema=SET_CAMERA_PARAMS_SCHEMA,
    )
