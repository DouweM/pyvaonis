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
                # Mosaic only applies to a *new* observation (set at start) — disable while busy.
                available_fn=lambda c: bool(c.data) and not c.data.is_busy,
            ),
            VaonisMultiNightSwitch(coordinator),
        ]
    )


class VaonisMultiLightSwitch(VaonisEntity, SwitchEntity):
    """BalENS (the app's HDR-background processing) — a persistent device setting."""

    _attr_translation_key = "multi_light"
    _attr_icon = "mdi:hdr"
    _attr_entity_category = EntityCategory.CONFIG  # a device setting, not a primary control

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
        """Enable BalENS (one-shot: take control, set, release)."""
        await self.coordinator.run_action(lambda c: c.set_multi_light(True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable BalENS (one-shot: take control, set, release)."""
        await self.coordinator.run_action(lambda c: c.set_multi_light(False))


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
        available_fn: Callable[[VaonisCoordinator], bool] | None = None,
    ) -> None:
        """Initialise the local option switch."""
        super().__init__(coordinator, key)
        self._attr_translation_key = key
        self._attr_icon = icon
        self._get = get_fn
        self._set = set_fn
        self._available_fn = available_fn

    async def async_added_to_hass(self) -> None:
        """Restore the last on/off choice into the coordinator."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self._set(self.coordinator, last.state == "on")

    @property
    def is_on(self) -> bool:
        """The current choice."""
        return self._get(self.coordinator)

    @property
    def available(self) -> bool:
        """Greyed out when the option can't take effect right now."""
        if not super().available:
            return False
        return self._available_fn is None or self._available_fn(self.coordinator)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the option."""
        self._set(self.coordinator, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the option."""
        self._set(self.coordinator, False)
        self.async_write_ha_state()


class VaonisMultiNightSwitch(VaonisEntity, SwitchEntity, RestoreEntity):
    """Multi-night: save the capture so it can be resumed on later nights.

    One control for both timings: while idle it's the choice the Observe button applies at start
    (``store.state``); turning it on *during* an observation also marks the running capture resumable
    (``setToBeResumable``) right away. (Turning it off can't un-mark a running capture — there's no
    such command — it only affects the next observation.)
    """

    _attr_translation_key = "multi_night"
    _attr_icon = "mdi:weather-night"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: VaonisCoordinator) -> None:
        """Initialise the multi-night switch."""
        super().__init__(coordinator, "multi_night")

    async def async_added_to_hass(self) -> None:
        """Restore the last choice into the coordinator."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self.coordinator.multi_night_enabled = last.state == "on"

    @property
    def is_on(self) -> bool:
        """On if the running capture is already resumable, else the next-observation choice."""
        if self.coordinator.client.current_observation() is not None:
            op = (
                self.coordinator.data.raw.get("currentOperation") if self.coordinator.data else {}
            ) or {}
            if (op.get("store") or {}).get("state") == "TO_BE_RESUMABLE":
                return True
        return self.coordinator.multi_night_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable multi-night; if observing, mark the running capture resumable now."""
        self.coordinator.multi_night_enabled = True
        self.async_write_ha_state()
        if self.coordinator.client.current_observation() is not None:
            await self.coordinator.run_action(lambda c: c.enable_multi_night())

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Clear the choice for the next observation (a running capture stays as-is)."""
        self.coordinator.multi_night_enabled = False
        self.async_write_ha_state()
