"""Switch platform: per-model device settings + local Mosaic/Multi-night observe toggles."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import VaonisConfigEntry
from .coordinator import VaonisCoordinator
from .entity import VaonisEntity
from .pyvaonis.const import model_supports


@dataclass(frozen=True)
class _SettingSwitch:
    """A boolean device setting (``SettingsBody`` field) exposed only on models that support it."""

    key: str  # entity key + translation_key
    field: str  # SettingsBody field, e.g. enableLiveFocus
    capability: str  # model capability gate (const.MODEL_SETTINGS), e.g. LIVE_FOCUS
    icon: str


# Each appears only on models whose InstrumentModelKt settings list includes the capability.
_SETTING_SWITCHES: tuple[_SettingSwitch, ...] = (
    _SettingSwitch("live_focus", "enableLiveFocus", "LIVE_FOCUS", "mdi:image-auto-adjust"),
    _SettingSwitch(
        "full_resolution", "enableFullResolution", "FULL_RESOLUTION", "mdi:quality-high"
    ),
    _SettingSwitch("dithering", "enableDithering", "DITHERING", "mdi:dots-grid"),
    _SettingSwitch("dark_usage", "enableDarkUsage", "DARKS", "mdi:image-filter-black-white"),
    _SettingSwitch("multi_light", "enableHdrBackground", "HDR_BACKGROUND", "mdi:hdr"),  # BalENS
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VaonisConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the supported device-setting switches + the Mosaic / Multi-night observe toggles."""
    coordinator = entry.runtime_data
    model = coordinator.model
    entities: list[SwitchEntity] = [
        VaonisSettingSwitch(coordinator, s)
        for s in _SETTING_SWITCHES
        if model_supports(model, s.capability)
    ]
    # Local toggles the Observe button reads (Advanced observation): mosaic + multi-night.
    entities.append(
        VaonisOptionSwitch(
            coordinator,
            "mosaic",
            "mdi:grid",
            lambda c: c.mosaic_enabled,
            lambda c, v: setattr(c, "mosaic_enabled", v),
            # Mosaic only applies to a *new* observation (set at start) — disable while busy, but it's
            # a local choice so it stays settable while the scope is offline (when it's not busy).
            available_fn=lambda c: not (c.data and c.data.is_busy),
        )
    )
    entities.append(VaonisMultiNightSwitch(coordinator))
    async_add_entities(entities)


class VaonisSettingSwitch(VaonisEntity, SwitchEntity):
    """A boolean device setting (``app/setSettings``) — only created for models that support it."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: VaonisCoordinator, setting: _SettingSwitch) -> None:
        """Initialise the setting switch."""
        super().__init__(coordinator, setting.key)
        self._attr_translation_key = setting.key
        self._attr_icon = setting.icon
        self._field = setting.field

    @property
    def is_on(self) -> bool:
        """Read the setting's boolean (a definite bool, else HA renders two on/off buttons)."""
        settings = self._status_value("settings")
        return bool(settings.get(self._field)) if isinstance(settings, dict) else False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the setting (one-shot: take control, set, release)."""
        await self.coordinator.run_action(lambda c: c.set_setting(self._field, True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the setting (one-shot: take control, set, release)."""
        await self.coordinator.run_action(lambda c: c.set_setting(self._field, False))


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
        """A local choice (not a device setting), so it works even while the scope is offline — only
        greyed out when it can't take effect (e.g. mosaic can't change mid-observation)."""
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
    def available(self) -> bool:
        """The next-observation choice is local, so it stays settable while offline; the live
        'mark the running capture resumable' path only runs when there's an observation (online)."""
        return True

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
