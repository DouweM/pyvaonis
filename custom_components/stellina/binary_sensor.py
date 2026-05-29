"""Binary sensor platform for the Stellina integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.binary_sensor import BinarySensorEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import StellinaConfigEntry
from .coordinator import StellinaCoordinator
from .entity import StellinaEntity


@dataclass(frozen=True, kw_only=True)
class StellinaBinaryDescription(BinarySensorEntityDescription):
    """Binary sensor description with a value extractor."""

    value_fn: Callable[[StellinaCoordinator], bool | None]


BINARY_SENSORS: tuple[StellinaBinaryDescription, ...] = (
    StellinaBinaryDescription(
        key="connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.client.connected,
    ),
    StellinaBinaryDescription(
        key="initialized",
        translation_key="initialized",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.data.initialized if c.data else None,
    ),
    StellinaBinaryDescription(
        key="has_control",
        translation_key="has_control",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.client.has_control,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StellinaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Stellina binary sensors."""
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [
        StellinaBinarySensor(coordinator, description) for description in BINARY_SENSORS
    ]
    entities.append(StellinaDarkSensor(coordinator, hass))
    async_add_entities(entities)


class StellinaBinarySensor(StellinaEntity, BinarySensorEntity):
    """A Stellina binary status sensor."""

    entity_description: StellinaBinaryDescription

    def __init__(
        self, coordinator: StellinaCoordinator, description: StellinaBinaryDescription
    ) -> None:
        """Initialise the binary sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the current state."""
        return self.entity_description.value_fn(self.coordinator)


class StellinaDarkSensor(StellinaEntity, BinarySensorEntity):
    """Whether it's dark enough to observe (Sun below -10deg), matching the app."""

    _attr_translation_key = "dark"

    def __init__(self, coordinator: StellinaCoordinator, hass: HomeAssistant) -> None:
        """Initialise the darkness sensor."""
        super().__init__(coordinator, "dark")
        self._hass = hass

    @property
    def is_on(self) -> bool:
        """True when observing is possible (it is dark)."""
        from pystellina import is_dark

        return is_dark(self._hass.config.latitude, self._hass.config.longitude)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose the Sun altitude and tonight's dark window."""
        from pystellina import observing_window
        from pystellina import sun_altitude

        lat, lon = self._hass.config.latitude, self._hass.config.longitude
        window = observing_window(lat, lon)
        attrs: dict[str, object] = {"sun_altitude": round(sun_altitude(lat, lon), 1)}
        if window:
            attrs["dark_start"] = window[0].isoformat()
            attrs["dark_end"] = window[1].isoformat()
        return attrs
