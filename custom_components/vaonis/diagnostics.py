"""Diagnostics for the Vaonis integration (HA's per-entry "Download diagnostics").

Bundles the current device status plus the telescope's per-operation telemetry reports, with
location/identifiers redacted so the download is safe to share in a bug report.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import VaonisConfigEntry

TO_REDACT = {
    "latitude",
    "longitude",
    "position",
    "telescopeId",
    "masterDeviceId",
    "deviceId",
    "observatoryId",
    "observatoryName",
    "connectedDevices",
    "ssid",
    "challenge",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: VaonisConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data: dict[str, Any] = {
        "connected": coordinator.client.connected,
        "status": coordinator.data.raw if coordinator.data else None,
    }
    try:  # read-only telemetry; never let a fetch failure block the download
        data["reports"] = await coordinator.client.available_reports()
    except Exception:  # diagnostics must never raise
        data["reports"] = None
    return async_redact_data(data, TO_REDACT)
