"""Switch platform: Multi-Light (device setting) + local Mosaic/Multi-night observe toggles."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import VaonisConfigEntry
from .coordinator import VaonisCoordinator
from .entity import VaonisEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VaonisConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Multi-Light switch and the Mosaic / Multi-night observe toggles."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            VaonisMultiLightSwitch(coordinator),
            # Local toggles the Observe button reads (Advanced observation): mosaic + multi-night.
            VaonisOptionSwitch(
                coordinator,
                "mosaic",
                "mdi:grid",
                lambda c: c.mosaic_enabled,
                lambda c, v: setattr(c, "mosaic_enabled", v),
            ),
            VaonisOptionSwitch(
                coordinator,
                "multi_night",
                "mdi:weather-night",
                lambda c: c.multi_night_enabled,
                lambda c, v: setattr(c, "multi_night_enabled", v),
            ),
        ]
    )


class VaonisMultiLightSwitch(VaonisEntity, SwitchEntity):
    """BalENS (the app's HDR-background processing) — a persistent device setting."""

    _attr_translation_key = "multi_light"
    _attr_icon = "mdi:hdr"

    def __init__(self, coordinator: VaonisCoordinator) -> None:
        """Initialise the switch."""
        super().__init__(coordinator, "multi_light")

    @property
    def is_on(self) -> bool:
        """Whether BalENS is on, from `settings.enableHdrBackground` (or the algo mode).

        Always a definite bool: returning None puts the switch in an "unknown" state, which HA renders
        as two on/off buttons instead of a single toggle.
        """
        settings = self._status_value("settings")
        if not isinstance(settings, dict):
            return False
        enabled = settings.get("enableHdrBackground")
        if isinstance(enabled, bool):
            return enabled
        # Some firmwares report only the algo mode; absent/NONE/OFF means off.
        algo = settings.get("algoHdrBackground")
        return bool(algo) and str(algo).upper() not in ("NONE", "OFF")

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable Multi-Light."""
        await self.coordinator.client.set_multi_light(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable Multi-Light."""
        await self.coordinator.client.set_multi_light(False)


class VaonisOptionSwitch(VaonisEntity, SwitchEntity, RestoreEntity):
    """A local on/off observe option (mosaic, multi-night) the Observe button reads.

    Not a device setting — it just holds a choice on the coordinator, restored across restarts.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: VaonisCoordinator,
        key: str,
        icon: str,
        get_fn: Callable[[VaonisCoordinator], bool],
        set_fn: Callable[[VaonisCoordinator, bool], None],
    ) -> None:
        """Initialise the local option switch."""
        super().__init__(coordinator, key)
        self._attr_translation_key = key
        self._attr_icon = icon
        self._get = get_fn
        self._set = set_fn

    async def async_added_to_hass(self) -> None:
        """Restore the last on/off choice into the coordinator."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self._set(self.coordinator, last.state == "on")

    @property
    def is_on(self) -> bool:
        """The current choice."""
        return self._get(self.coordinator)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the option."""
        self._set(self.coordinator, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the option."""
        self._set(self.coordinator, False)
        self.async_write_ha_state()
