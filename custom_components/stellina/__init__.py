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
        vol.Optional("wait_for_dark", default=True): bool,
        vol.Optional("require_dark", default=True): bool,
        vol.Optional("park", default=True): bool,
        vol.Optional("shutdown", default=False): bool,
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
        """Run an unattended observing plan in the background (gate weather in the automation)."""
        from pystellina import SequenceItem
        from pystellina import run_sequence

        coordinator = _first_coordinator()
        if coordinator.plan_task is not None and not coordinator.plan_task.done():
            raise HomeAssistantError("a plan is already running (call stellina.stop_plan first)")
        lat, lon = hass.config.latitude, hass.config.longitude
        items = [SequenceItem.parse(t) for t in call.data["targets"]]

        async def _runner() -> None:
            try:
                await run_sequence(
                    coordinator.client,
                    items,
                    latitude=lat,
                    longitude=lon,
                    require_dark=call.data["require_dark"],
                    wait_for_dark=call.data["wait_for_dark"],
                    park_at_end=call.data["park"],
                    shutdown_at_end=call.data["shutdown"],
                    on_event=lambda e: _LOGGER.info("plan %s %s %s", e.kind, e.item, e.detail),
                )
            except Exception:
                _LOGGER.exception("Stellina plan failed")

        coordinator.plan_task = hass.async_create_background_task(_runner(), "stellina_plan")
        return {"started": True, "targets": call.data["targets"]}

    async def stop_plan(call: ServiceCall) -> None:
        """Cancel a running plan and stop the current observation."""
        coordinator = _first_coordinator()
        if coordinator.plan_task is not None:
            coordinator.plan_task.cancel()
            coordinator.plan_task = None
        with suppress(Exception):
            await coordinator.client.stop_observation()

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
