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

from .coordinator import VaonisConfigEntry
from .coordinator import VaonisCoordinator
from .entity import VaonisEntity


@dataclass(frozen=True, kw_only=True)
class VaonisBinaryDescription(BinarySensorEntityDescription):
    """Binary sensor description with a value extractor."""

    value_fn: Callable[[VaonisCoordinator], bool | None]
    # Stay available even when the telescope is unreachable (so it can report the disconnect itself
    # as "off" rather than going Unavailable along with everything else).
    always_available: bool = False


BINARY_SENSORS: tuple[VaonisBinaryDescription, ...] = (
    VaonisBinaryDescription(
        key="connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.client.connected,
        always_available=True,
    ),
    VaonisBinaryDescription(
        # Primary (not diagnostic): being initialised/aligned gates the whole observing flow.
        key="initialized",
        translation_key="initialized",
        icon="mdi:crosshairs-gps",
        value_fn=lambda c: c.data.initialized if c.data else None,
    ),
    VaonisBinaryDescription(
        key="has_control",
        translation_key="has_control",
        icon="mdi:remote",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.client.has_control,
    ),
    VaonisBinaryDescription(
        key="tracking",
        translation_key="tracking",
        icon="mdi:radar",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: any(
            (m or {}).get("state") == "TRACKING"
            for m in ((c.data.raw.get("motors") or {}).values() if c.data else [])
        ),
    ),
    VaonisBinaryDescription(
        key="defog",
        translation_key="defog",
        icon="mdi:weather-fog",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: (
            (c.data.raw.get("sensors") or {}).get("defogStatus", "OFF") != "OFF" if c.data else None
        ),
    ),
    VaonisBinaryDescription(
        key="update_available",
        translation_key="update_available",
        device_class=BinarySensorDeviceClass.UPDATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: _update_available(c.data.raw.get("update") or {}) if c.data else None,
    ),
)


def _update_available(update: dict[str, object]) -> bool | None:
    """True when the installed firmware differs from an available one (status.update)."""
    installed = update.get("installedVersion")
    available = update.get("availableVersion") or update.get("latestVersion")
    if not installed or not available:
        return False
    return installed != available


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VaonisConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Stellina binary sensors."""
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [
        VaonisBinarySensor(coordinator, description) for description in BINARY_SENSORS
    ]
    entities.append(VaonisDarkSensor(coordinator, hass))
    async_add_entities(entities)


class VaonisBinarySensor(VaonisEntity, BinarySensorEntity):
    """A Stellina binary status sensor."""

    entity_description: VaonisBinaryDescription

    def __init__(
        self, coordinator: VaonisCoordinator, description: VaonisBinaryDescription
    ) -> None:
        """Initialise the binary sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the current state."""
        return self.entity_description.value_fn(self.coordinator)

    @property
    def available(self) -> bool:
        """Connectivity stays available (to report 'off' when disconnected); others follow the coordinator."""
        if self.entity_description.always_available:
            return True
        return super().available


class VaonisDarkSensor(VaonisEntity, BinarySensorEntity):
    """Whether it's dark enough to observe (Sun below -10deg), matching the app."""

    _attr_translation_key = "dark"
    _attr_icon = "mdi:weather-night"

    def __init__(self, coordinator: VaonisCoordinator, hass: HomeAssistant) -> None:
        """Initialise the darkness sensor."""
        super().__init__(coordinator, "dark")
        self._hass = hass

    @property
    def is_on(self) -> bool:
        """True when observing is possible (it is dark)."""
        from .pyvaonis import is_dark

        return is_dark(self._hass.config.latitude, self._hass.config.longitude)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose the Sun altitude and tonight's dark window."""
        from .pyvaonis import observing_window
        from .pyvaonis import sun_altitude

        lat, lon = self._hass.config.latitude, self._hass.config.longitude
        window = observing_window(lat, lon)
        attrs: dict[str, object] = {"sun_altitude": round(sun_altitude(lat, lon), 1)}
        if window:
            attrs["dark_start"] = window[0].isoformat()
            attrs["dark_end"] = window[1].isoformat()
        return attrs
