"""Switch platform: Multi-Light (CovalENS / HDR background) toggle."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import VaonisConfigEntry
from .coordinator import VaonisCoordinator
from .entity import VaonisEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VaonisConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Multi-Light switch."""
    async_add_entities([VaonisMultiLightSwitch(entry.runtime_data)])


class VaonisMultiLightSwitch(VaonisEntity, SwitchEntity):
    """Multi-Light (HDR background / CovalENS) — a persistent device setting."""

    _attr_translation_key = "multi_light"

    def __init__(self, coordinator: VaonisCoordinator) -> None:
        """Initialise the switch."""
        super().__init__(coordinator, "multi_light")

    @property
    def is_on(self) -> bool | None:
        """Read `settings.enableHdrBackground` from the latest status."""
        settings = self._status_value("settings")
        if isinstance(settings, dict):
            return settings.get("enableHdrBackground")
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable Multi-Light."""
        await self.coordinator.client.set_multi_light(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable Multi-Light."""
        await self.coordinator.client.set_multi_light(False)
