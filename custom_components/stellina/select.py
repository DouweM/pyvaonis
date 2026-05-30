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
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from pystellina import visibility_rating
from pystellina import visible_now

from .coordinator import StellinaConfigEntry
from .coordinator import StellinaCoordinator
from .entity import StellinaEntity

MIN_ALTITUDE = 15.0
MIN_GRADE = 5.0
MAX_OPTIONS = 25


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StellinaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the target select."""
    async_add_entities([StellinaTargetSelect(entry.runtime_data, hass)])


class StellinaTargetSelect(StellinaEntity, SelectEntity):
    """Choose tonight's observation target."""

    _attr_translation_key = "target"

    def __init__(self, coordinator: StellinaCoordinator, hass: HomeAssistant) -> None:
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
                "visibility": visibility_rating(v.altitude),  # good / poor / not_visible (app's color)
                "grade": v.obj.grade,
                "magnitude": v.obj.magnitude,
                "constellation": v.obj.constellation,
                "category": v.obj.category,
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
        """Take control and start observing the chosen object."""
        client = self.coordinator.client
        await client.take_control()
        await client.observe_object(option, replace=True)
        self._attr_current_option = option
        self.async_write_ha_state()
