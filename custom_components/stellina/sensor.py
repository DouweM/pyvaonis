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


def _total_stacking(coordinator: StellinaCoordinator) -> Any:
    obs = coordinator.client.current_observation()
    return obs.total_stacking_count if obs else None


def _raw(*path: str) -> Any:
    def getter(coordinator: StellinaCoordinator) -> Any:
        node: Any = coordinator.data.raw if coordinator.data else {}
        for key in path:
            if not isinstance(node, dict):
                return None
            node = node.get(key)
        return node

    return getter


def _storage_free_mb(coordinator: StellinaCoordinator) -> Any:
    avail = _raw("storage", "data", "available")(coordinator)
    return round(avail / 1000) if isinstance(avail, int | float) else None


def _controlling_device(coordinator: StellinaCoordinator) -> Any:
    raw = coordinator.data.raw if coordinator.data else {}
    master = raw.get("masterDeviceId")
    for dev in raw.get("connectedDevices") or []:
        if dev.get("id") == master:
            return dev.get("name") or master
    return master


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
    StellinaSensorDescription(
        key="total_stacking",
        translation_key="total_stacking",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_total_stacking,
    ),
    StellinaSensorDescription(
        key="storage_free",
        translation_key="storage_free",
        native_unit_of_measurement="MB",
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_storage_free_mb,
    ),
    StellinaSensorDescription(
        key="band",
        translation_key="band",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_raw("network", "band"),
    ),
    StellinaSensorDescription(
        key="filter",
        translation_key="filter",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_raw("filter"),
    ),
    StellinaSensorDescription(
        key="autofocus_temperature",
        translation_key="autofocus_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_raw("autofocusTemperature"),
    ),
    StellinaSensorDescription(
        key="controlling_device",
        translation_key="controlling_device",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_controlling_device,
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
