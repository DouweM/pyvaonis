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
from .coordinator import StellinaConfigEntry
from .coordinator import StellinaCoordinator
from .http import register_view

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

SERVICE_EXPORT_CAPTURE = "export_capture"
SERVICE_RUN_PLAN = "run_plan"
SERVICE_STOP_PLAN = "stop_plan"
SERVICE_OBSERVE = "observe"
OBSERVE_SCHEMA = vol.Schema(
    {vol.Required("target"): str, vol.Optional("allow_solar", default=False): bool}
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


async def async_setup_entry(hass: HomeAssistant, entry: StellinaConfigEntry) -> bool:
    """Set up Stellina from a config entry."""
    coordinator = StellinaCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    register_view(hass)
    _register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: StellinaConfigEntry) -> bool:
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
        data = await client.export_capture(capture_id, fmt)

        media_root = hass.config.media_dirs.get("local") or hass.config.path("media")
        out_dir = Path(media_root) / "stellina"
        await hass.async_add_executor_job(functools.partial(os.makedirs, out_dir, exist_ok=True))
        out_path = out_dir / f"{capture_id}.{'tif' if fmt == 'tiff' else 'jxl'}"
        await hass.async_add_executor_job(out_path.write_bytes, data)
        return {"file": str(out_path), "bytes": len(data)}

    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT_CAPTURE,
        export_capture,
        schema=EXPORT_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    def _first_coordinator() -> StellinaCoordinator:
        entries = [
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if getattr(e, "runtime_data", None) is not None
        ]
        if not entries:
            raise HomeAssistantError("No Stellina telescope is set up")
        return entries[0].runtime_data

    async def run_plan(call: ServiceCall) -> ServiceResponse:
        """Start the telescope's native autonomous plan (gate weather in the automation)."""
        from datetime import UTC
        from datetime import datetime

        from pystellina import PlanItem
        from pystellina import observing_window

        coordinator = _first_coordinator()
        lat, lon = hass.config.latitude, hass.config.longitude
        items = [PlanItem.parse(t) for t in call.data["targets"]]

        start_time = None
        if call.data["wait_for_dark"]:
            window = observing_window(lat, lon)
            if window is not None:
                start_time = max(window[0], datetime.now(UTC))

        await coordinator.client.take_control()
        await coordinator.client.start_plan(
            items, name="Home Assistant plan", latitude=lat, longitude=lon, start_time=start_time
        )
        return {"started": True, "targets": call.data["targets"]}

    async def stop_plan(call: ServiceCall) -> None:
        """Cancel the running native plan."""
        coordinator = _first_coordinator()
        with suppress(Exception):
            await coordinator.client.stop_plan()

    hass.services.async_register(
        DOMAIN,
        SERVICE_RUN_PLAN,
        run_plan,
        schema=RUN_PLAN_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(DOMAIN, SERVICE_STOP_PLAN, stop_plan)

    async def observe(call: ServiceCall) -> None:
        """Slew to a catalog object (by id/name/designation) and start imaging."""
        await _first_coordinator().client.observe_object(
            call.data["target"], allow_solar=call.data["allow_solar"], replace=True
        )

    hass.services.async_register(DOMAIN, SERVICE_OBSERVE, observe, schema=OBSERVE_SCHEMA)
