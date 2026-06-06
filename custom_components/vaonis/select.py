"""Select platform: pick a target to observe from the bundled catalog.

The options are the catalog objects currently above the horizon for Home Assistant's
configured location, best (highest-graded) first. Selecting one takes control and starts
an observation with that object's recommended settings.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import VaonisConfigEntry
from .coordinator import VaonisCoordinator
from .entity import VaonisEntity
from .pyvaonis import VaonisError
from .pyvaonis import visibility_rating
from .pyvaonis import visible_now

MIN_ALTITUDE = 15.0
MIN_GRADE = 5.0
MAX_OPTIONS = 25


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VaonisConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the target select."""
    async_add_entities([VaonisTargetSelect(entry.runtime_data, hass)])


class VaonisTargetSelect(VaonisEntity, SelectEntity):
    """Choose tonight's observation target."""

    _attr_translation_key = "target"

    def __init__(self, coordinator: VaonisCoordinator, hass: HomeAssistant) -> None:
        """Initialise the select and compute the first option set."""
        super().__init__(coordinator, "target")
        self._hass = hass
        self._attr_current_option = None
        self._attr_options = []
        self._suggestions: list[dict[str, Any]] = []
        self._refresh_options()

    def _refresh_options(self) -> None:
        # require_dark=True => no targets offered until it's actually dark (like the app).
        visible = visible_now(
            self._hass.config.latitude,
            self._hass.config.longitude,
            min_altitude=MIN_ALTITUDE,
            min_grade=MIN_GRADE,
            limit=MAX_OPTIONS,
            require_dark=True,
        )
        self._attr_options = [v.obj.display_name for v in visible]
        self._suggestions = [
            {
                "name": v.obj.display_name,
                "altitude": round(v.altitude, 1),
                "visibility": visibility_rating(
                    v.altitude
                ),  # good / poor / not_visible (app's color)
                "recommended_minutes": v.obj.duration or None,
                "grade": v.obj.grade,
                "magnitude": v.obj.magnitude,
                "constellation": v.obj.constellation_name or v.obj.constellation,
                "category": v.obj.category_label or v.obj.category,
                "distance": v.obj.distance_display,
                "is_solar": v.obj.is_solar,
                "description": v.obj.description,
            }
            for v in visible
        ]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the ranked suggestions (with altitude/grade) for dashboards."""
        return {"suggestions": self._suggestions}

    @callback
    def _handle_coordinator_update(self) -> None:
        self._refresh_options()
        super()._handle_coordinator_update()

    async def async_select_option(self, option: str) -> None:
        """Take control, start observing the chosen object, then release (one-shot)."""
        try:
            await self.coordinator.run_action(lambda c: c.observe_object(option, replace=True))
        except VaonisError as err:
            raise HomeAssistantError(str(err)) from err
        self._attr_current_option = option
        self.async_write_ha_state()
