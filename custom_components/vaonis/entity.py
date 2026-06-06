"""Shared entity base for the Stellina integration."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .const import MANUFACTURER
from .const import MODEL
from .coordinator import VaonisCoordinator


class VaonisEntity(CoordinatorEntity[VaonisCoordinator]):
    """Base entity tying everything to a single telescope device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: VaonisCoordinator, key: str) -> None:
        """Initialise common attributes."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._telescope_id}_{key}"

    @property
    def _telescope_id(self) -> str:
        status = self.coordinator.data
        return (status.telescope_id if status and status.telescope_id else None) or (
            self.coordinator.config_entry.unique_id or "unknown"
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Describe the telescope as a single HA device (model resolved from status)."""
        from .pyvaonis import model_display_name

        status = self.coordinator.data
        model = model_display_name(status.model if status else None)
        name = MODEL
        sw_version = None
        if status:
            name = (status.raw.get("settings") or {}).get("telescopeName") or model
            sw_version = status.raw.get("version")
        return DeviceInfo(
            identifiers={(DOMAIN, self._telescope_id)},
            manufacturer=MANUFACTURER,
            model=model,
            name=name,
            sw_version=sw_version,
        )

    def _status_value(self, *path: str) -> Any:
        """Safely dig a nested value out of the raw status payload."""
        node: Any = self.coordinator.data.raw if self.coordinator.data else {}
        for key in path:
            if not isinstance(node, dict):
                return None
            node = node.get(key)
        return node
