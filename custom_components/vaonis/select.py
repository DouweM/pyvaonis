"""Select platform: pick a target to observe from the bundled catalog.

The options are the catalog objects currently above the horizon for Home Assistant's
configured location, best (highest-graded) first. Selecting one only *chooses* the target
(no telescope movement) — pressing the **Observe** button then starts it, mirroring the
app's browse-then-Observe flow.
"""

from __future__ import annotations

import time
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import VaonisConfigEntry
from .coordinator import VaonisCoordinator
from .entity import VaonisEntity
from .pyvaonis import VaonisError
from .pyvaonis import get_object
from .pyvaonis import visibility_rating
from .pyvaonis import visible_tonight
from .pyvaonis.const import model_supports

_REFRESH_TTL = (
    600.0  # recompute tonight's visibility at most this often (it's stable for the night)
)


def _target_label(obj: Any, peak: float | None = None, peak_time: str | None = None) -> str:
    """Dropdown label: ``name · ↑peak° at time · type · mag · minutes``.

    We bake the key planning info into the option string because HA's select UI shows nothing but
    that string. Tonight's *peak* altitude is used (not the live instantaneous altitude), so the label
    is stable through the night and doesn't churn the option set; the full per-target breakdown still
    lives in the ``suggestions`` attribute for a custom card.
    """
    bits: list[str] = []
    if peak is not None:
        bits.append(f"↑{round(peak)}°" + (f" at {peak_time}" if peak_time else ""))
    category = obj.category_label or obj.category
    if category and category.lower() not in obj.display_name.lower():
        bits.append(category)
    if obj.magnitude is not None:
        bits.append(f"mag {obj.magnitude:.1f}")
    if obj.duration:
        bits.append(f"{obj.duration} min")
    return f"{obj.display_name} · {' · '.join(bits)}" if bits else obj.display_name


MIN_ALTITUDE = 15.0
MIN_GRADE = 5.0
MAX_OPTIONS = 25

# BalENS level: friendly labels are the select options; mapped back to wire values on set
# (set_balens_level normalises "First Edition" -> OLD, etc.).
_BALENS_LABELS = {
    "RECOMMENDED": "Recommended",
    "SOFT": "Soft",
    "HARD": "Hard",
    "OLD": "First Edition",
}
_BRIGHTNESS_LABELS = {"LOW": "Low", "MEDIUM": "Medium", "HIGH": "High"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VaonisConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the target select (+ BalENS level / button brightness where the model supports them)."""
    coordinator = entry.runtime_data
    model = coordinator.data.model if coordinator.data else None
    selects: list[SelectEntity] = [VaonisTargetSelect(coordinator, hass)]
    if model_supports(model, "HDR_BACKGROUND"):  # BalENS is Vespera-Pro-only
        selects.append(VaonisBalensLevelSelect(coordinator))
    if model_supports(model, "BTN_BRIGHTNESS"):  # LED brightness (Vespera / Vespera Pro)
        selects.append(VaonisButtonBrightnessSelect(coordinator))
    async_add_entities(selects)


class VaonisSettingSelect(VaonisEntity, SelectEntity):
    """A device-setting picker (``app/setSettings``) with friendly labels mapped to wire values."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: VaonisCoordinator,
        key: str,
        icon: str,
        field: str,
        labels: dict[str, str],
    ) -> None:
        """Initialise a settings select (``labels`` maps wire value -> display label)."""
        super().__init__(coordinator, key)
        self._attr_translation_key = key
        self._attr_icon = icon
        self._field = field
        self._labels = labels
        self._attr_options = list(labels.values())

    @property
    def current_option(self) -> str | None:
        """The setting's current value as a friendly label, or None if unknown."""
        settings = self._status_value("settings")
        value = settings.get(self._field) if isinstance(settings, dict) else None
        return self._labels.get(value) if value is not None else None

    async def async_select_option(self, option: str) -> None:
        """Set the setting (one-shot: take control, set, release)."""
        wire = next((k for k, label in self._labels.items() if label == option), option)
        try:
            await self.coordinator.run_action(lambda c: c.set_setting(self._field, wire))
        except VaonisError as err:
            raise HomeAssistantError(str(err)) from err


class VaonisButtonBrightnessSelect(VaonisSettingSelect):
    """The telescope's LED button brightness — Low / Medium / High."""

    def __init__(self, coordinator: VaonisCoordinator) -> None:
        """Initialise the button-brightness select."""
        super().__init__(
            coordinator,
            "button_brightness",
            "mdi:brightness-6",
            "buttonBrightness",
            _BRIGHTNESS_LABELS,
        )


class VaonisBalensLevelSelect(VaonisEntity, SelectEntity):
    """BalENS (HDR background) processing level — Recommended / Soft / Hard / First Edition."""

    _attr_translation_key = "balens_level"
    _attr_icon = "mdi:hdr"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: VaonisCoordinator) -> None:
        """Initialise the BalENS-level select."""
        super().__init__(coordinator, "balens_level")
        self._attr_options = list(_BALENS_LABELS.values())

    @property
    def current_option(self) -> str | None:
        """The level from `settings.algoHdrBackground` as a friendly label, or None if unknown."""
        settings = self._status_value("settings")
        algo = settings.get("algoHdrBackground") if isinstance(settings, dict) else None
        return _BALENS_LABELS.get(algo) if algo is not None else None

    async def async_select_option(self, option: str) -> None:
        """Set the BalENS level (one-shot: take control, set, release)."""
        try:
            await self.coordinator.run_action(lambda c: c.set_balens_level(option))
        except VaonisError as err:
            raise HomeAssistantError(str(err)) from err


class VaonisTargetSelect(VaonisEntity, SelectEntity):
    """Choose an observation target (curated, currently-up deep-sky objects + planets/Moon)."""

    _attr_translation_key = "target"
    _attr_icon = "mdi:star-shooting"

    def __init__(self, coordinator: VaonisCoordinator, hass: HomeAssistant) -> None:
        """Initialise the select and compute the first option set."""
        super().__init__(coordinator, "target")
        self._hass = hass
        self._attr_current_option = None
        self._attr_options = []
        self._target_by_label: dict[str, str] = {}  # rich option label -> catalog target name
        self._suggestions: list[dict[str, Any]] = []
        self._computed_at = 0.0  # monotonic time of the last (throttled) recompute
        self._refresh_options()

    def _refresh_options(self) -> None:
        # What's worth imaging *tonight* — peak altitude over tonight's dark window (not the live
        # instant), so a target picked in daylight reflects the coming night. Curated (grade>=5)
        # deep-sky + planets/Moon, best first. Recompute is throttled (it's stable through the night
        # and would otherwise run on every status push); current_option is kept fresh by select.
        now = time.monotonic()
        if self._attr_options and now - self._computed_at < _REFRESH_TTL:
            return
        self._computed_at = now
        visible = visible_tonight(
            self._hass.config.latitude,
            self._hass.config.longitude,
            min_altitude=MIN_ALTITUDE,
            min_grade=MIN_GRADE,
            limit=MAX_OPTIONS,
        )
        self._target_by_label = {}
        self._suggestions = []
        for v in visible:
            best = dt_util.as_local(v.peak_time).strftime("%H:%M")
            label = _target_label(v.obj, peak=v.peak_altitude, peak_time=best)
            self._target_by_label[label] = v.obj.display_name
            self._suggestions.append(
                {
                    "name": v.obj.display_name,
                    "peak_altitude": round(v.peak_altitude, 1),
                    "peak_time": v.peak_time.isoformat(),
                    "visibility": visibility_rating(v.peak_altitude),  # good/poor (app's colour)
                    "up_now": v.up_now,
                    "recommended_minutes": v.obj.duration or None,
                    "grade": v.obj.grade,
                    "magnitude": v.obj.magnitude,
                    "constellation": v.obj.constellation_name or v.obj.constellation,
                    "category": v.obj.category_label or v.obj.category,
                    "distance": v.obj.distance_display,
                    "is_solar": v.obj.is_solar,
                    "description": v.obj.description,
                }
            )
        self._attr_options = list(self._target_by_label)
        # Keep the current pick selectable even if it isn't in tonight's list, so the chosen target
        # (and the Observe button that reads it) stays valid. Match by target name.
        chosen = self.coordinator.selected_target
        current = next((lbl for lbl, t in self._target_by_label.items() if t == chosen), None)
        if chosen and current is None:
            obj = get_object(chosen)
            current = _target_label(obj) if obj else chosen
            self._target_by_label[current] = chosen
            self._attr_options.append(current)
        self._attr_current_option = current

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the ranked suggestions (with altitude/grade) for dashboards."""
        return {"suggestions": self._suggestions}

    @callback
    def _handle_coordinator_update(self) -> None:
        self._refresh_options()
        super()._handle_coordinator_update()

    async def async_select_option(self, option: str) -> None:
        """Choose the target (no telescope movement) — the Observe button starts it."""
        self.coordinator.selected_target = self._target_by_label.get(option, option)
        self._attr_current_option = option
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Available when idle with targets to offer (you pick the next observation)."""
        if not super().available:
            return False
        idle = bool(self.coordinator.data) and not self.coordinator.data.is_busy
        return idle and bool(self._attr_options)
