"""The Stellina integration."""

from __future__ import annotations

import functools
import os
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

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.SELECT,
    Platform.SENSOR,
]

SERVICE_EXPORT_CAPTURE = "export_capture"
EXPORT_SCHEMA = vol.Schema(
    {
        vol.Optional("capture_id"): str,
        vol.Optional("format", default="tiff"): vol.In(["tiff", "jxl"]),
    }
)


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
