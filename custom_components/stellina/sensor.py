"""Sensor platform for the Stellina integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.sensor import SensorEntityDescription
from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import PERCENTAGE
from homeassistant.const import EntityCategory
from homeassistant.const import UnitOfTemperature
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import StellinaConfigEntry
from .coordinator import StellinaCoordinator
from .entity import StellinaEntity


@dataclass(frozen=True, kw_only=True)
class StellinaSensorDescription(SensorEntityDescription):
    """Sensor description with a value extractor over the coordinator."""

    value_fn: Callable[[StellinaCoordinator], Any]


def _sensors_field(key: str) -> Any:
    def getter(coordinator: StellinaCoordinator) -> Any:
        sensors = coordinator.data.raw.get("sensors") if coordinator.data else None
        return sensors.get(key) if isinstance(sensors, dict) else None

    return getter


def _operation(coordinator: StellinaCoordinator) -> Any:
    op = coordinator.data.raw.get("currentOperation") if coordinator.data else None
    if isinstance(op, dict) and not op.get("stopped"):
        return op.get("type")
    return None


def _target(coordinator: StellinaCoordinator) -> Any:
    obs = coordinator.client.current_observation()
    return obs.object_name if obs else None


def _step(coordinator: StellinaCoordinator) -> Any:
    obs = coordinator.client.current_observation()
    return obs.current_step if obs else None


def _stacking(coordinator: StellinaCoordinator) -> Any:
    obs = coordinator.client.current_observation()
    return obs.stacking_count if obs else None


def _integration(coordinator: StellinaCoordinator) -> Any:
    obs = coordinator.client.current_observation()
    return round(obs.integration_seconds) if obs and obs.integration_seconds else None


SENSORS: tuple[StellinaSensorDescription, ...] = (
    StellinaSensorDescription(
        key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_sensors_field("temperature"),
    ),
    StellinaSensorDescription(
        key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_sensors_field("humidity"),
    ),
    StellinaSensorDescription(
        key="dewpoint_depression",
        translation_key="dewpoint_depression",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_sensors_field("dewpointDepression"),
    ),
    StellinaSensorDescription(
        key="operation",
        translation_key="operation",
        value_fn=_operation,
    ),
    StellinaSensorDescription(
        key="target",
        translation_key="target",
        value_fn=_target,
    ),
    StellinaSensorDescription(
        key="step",
        translation_key="step",
        value_fn=_step,
    ),
    StellinaSensorDescription(
        key="stacking_count",
        translation_key="stacking_count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_stacking,
    ),
    StellinaSensorDescription(
        key="integration",
        translation_key="integration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_integration,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StellinaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Stellina sensors."""
    coordinator = entry.runtime_data
    async_add_entities(StellinaSensor(coordinator, description) for description in SENSORS)


class StellinaSensor(StellinaEntity, SensorEntity):
    """A Stellina status sensor."""

    entity_description: StellinaSensorDescription

    def __init__(
        self, coordinator: StellinaCoordinator, description: StellinaSensorDescription
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the current value."""
        return self.entity_description.value_fn(self.coordinator)
