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

from .coordinator import VaonisConfigEntry
from .coordinator import VaonisCoordinator
from .entity import VaonisEntity


@dataclass(frozen=True, kw_only=True)
class VaonisSensorDescription(SensorEntityDescription):
    """Sensor description with a value extractor over the coordinator.

    ``available_fn`` lets a sensor report *unavailable* (rather than an "Unknown" None) when it has
    no applicable value — e.g. observation stats while the scope is idle.
    """

    value_fn: Callable[[VaonisCoordinator], Any]
    available_fn: Callable[[VaonisCoordinator], bool] | None = None


def _observing(coordinator: VaonisCoordinator) -> bool:
    """Whether an observation is currently running (its live stats are meaningful)."""
    return coordinator.client.current_observation() is not None


def _planning(coordinator: VaonisCoordinator) -> bool:
    """Whether a native plan is currently running."""
    return coordinator.client.plan_progress() is not None


def _initializing(coordinator: VaonisCoordinator) -> bool:
    """Whether an auto-init is currently in progress."""
    return coordinator.client.autoinit_step() is not None


def _sensors_field(key: str) -> Any:
    def getter(coordinator: VaonisCoordinator) -> Any:
        sensors = coordinator.data.raw.get("sensors") if coordinator.data else None
        return sensors.get(key) if isinstance(sensors, dict) else None

    return getter


def _operation(coordinator: VaonisCoordinator) -> Any:
    op = coordinator.data.raw.get("currentOperation") if coordinator.data else None
    if isinstance(op, dict) and not op.get("stopped"):
        return op.get("type")
    return None


def _target(coordinator: VaonisCoordinator) -> Any:
    obs = coordinator.client.current_observation()
    return obs.object_name if obs else None


def _step(coordinator: VaonisCoordinator) -> Any:
    obs = coordinator.client.current_observation()
    return obs.current_step if obs else None


def _stacking(coordinator: VaonisCoordinator) -> Any:
    obs = coordinator.client.current_observation()
    return obs.stacking_count if obs else None


def _integration(coordinator: VaonisCoordinator) -> Any:
    obs = coordinator.client.current_observation()
    return round(obs.integration_seconds) if obs and obs.integration_seconds else None


def _total_stacking(coordinator: VaonisCoordinator) -> Any:
    obs = coordinator.client.current_observation()
    return obs.total_stacking_count if obs else None


def _raw(*path: str) -> Any:
    def getter(coordinator: VaonisCoordinator) -> Any:
        node: Any = coordinator.data.raw if coordinator.data else {}
        for key in path:
            if not isinstance(node, dict):
                return None
            node = node.get(key)
        return node

    return getter


def _storage_free_mb(coordinator: VaonisCoordinator) -> Any:
    avail = _raw("storage", "data", "available")(coordinator)
    return round(avail / 1000) if isinstance(avail, int | float) else None


def _capture(coordinator: VaonisCoordinator) -> dict[str, Any] | None:
    op = coordinator.data.raw.get("currentOperation") if coordinator.data else None
    if isinstance(op, dict) and op.get("type") == "OBSERVATION" and not op.get("stopped"):
        cap = op.get("capture")
        return cap if isinstance(cap, dict) else None
    return None


def _gain(coordinator: VaonisCoordinator) -> Any:
    cap = _capture(coordinator)
    return ((cap or {}).get("cameraParams") or {}).get("gain") if cap else None


def _exposure_seconds(coordinator: VaonisCoordinator) -> Any:
    cap = _capture(coordinator)
    us = ((cap or {}).get("cameraParams") or {}).get("exposureMicroSec") if cap else None
    return round(us / 1_000_000, 1) if isinstance(us, int | float) else None


def _frames_acquired(coordinator: VaonisCoordinator) -> Any:
    cap = _capture(coordinator)
    return cap.get("acquisitionCount") if cap else None


def _plan_state(coordinator: VaonisCoordinator) -> Any:
    plan = coordinator.client.plan_progress()
    return plan.state if plan else None


def _plan_target(coordinator: VaonisCoordinator) -> Any:
    plan = coordinator.client.plan_progress()
    if not plan:
        return None
    if plan.current_target and plan.current_index is not None:
        return f"{plan.current_target} ({plan.current_index + 1}/{plan.target_count})"
    return plan.current_target


def _status_summary(coordinator: VaonisCoordinator) -> Any:
    return coordinator.client.status_summary()


def _init_step(coordinator: VaonisCoordinator) -> Any:
    return coordinator.client.autoinit_step()


def _controlling_device(coordinator: VaonisCoordinator) -> Any:
    raw = coordinator.data.raw if coordinator.data else {}
    master = raw.get("masterDeviceId")
    for dev in raw.get("connectedDevices") or []:
        if dev.get("id") == master:
            return dev.get("name") or master
    return master


SENSORS: tuple[VaonisSensorDescription, ...] = (
    VaonisSensorDescription(
        key="status",
        translation_key="status",
        value_fn=_status_summary,
    ),
    VaonisSensorDescription(
        key="init_step",
        translation_key="init_step",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_init_step,
        available_fn=_initializing,
    ),
    VaonisSensorDescription(
        key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_sensors_field("temperature"),
    ),
    VaonisSensorDescription(
        key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_sensors_field("humidity"),
    ),
    VaonisSensorDescription(
        key="dewpoint_depression",
        translation_key="dewpoint_depression",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_sensors_field("dewpointDepression"),
    ),
    VaonisSensorDescription(
        key="operation",
        translation_key="operation",
        value_fn=_operation,
        available_fn=lambda c: _operation(c) is not None,
    ),
    VaonisSensorDescription(
        key="target",
        translation_key="target",
        value_fn=_target,
        available_fn=_observing,
    ),
    VaonisSensorDescription(
        key="step",
        translation_key="step",
        value_fn=_step,
        available_fn=_observing,
    ),
    VaonisSensorDescription(
        key="stacking_count",
        translation_key="stacking_count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_stacking,
        available_fn=_observing,
    ),
    VaonisSensorDescription(
        key="integration",
        translation_key="integration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_integration,
        available_fn=_observing,
    ),
    VaonisSensorDescription(
        key="total_stacking",
        translation_key="total_stacking",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_total_stacking,
        available_fn=_observing,
    ),
    VaonisSensorDescription(
        key="storage_free",
        translation_key="storage_free",
        native_unit_of_measurement="MB",
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_storage_free_mb,
    ),
    VaonisSensorDescription(
        key="band",
        translation_key="band",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_raw("network", "band"),
    ),
    VaonisSensorDescription(
        key="filter",
        translation_key="filter",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_raw("filter"),
    ),
    VaonisSensorDescription(
        key="autofocus_temperature",
        translation_key="autofocus_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_raw("autofocusTemperature"),
    ),
    VaonisSensorDescription(
        key="controlling_device",
        translation_key="controlling_device",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_controlling_device,
    ),
    VaonisSensorDescription(
        key="frames_acquired",
        translation_key="frames_acquired",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_frames_acquired,
        available_fn=_observing,
    ),
    VaonisSensorDescription(
        key="gain",
        translation_key="gain",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_gain,
        available_fn=_observing,
    ),
    VaonisSensorDescription(
        key="exposure",
        translation_key="exposure",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_exposure_seconds,
        available_fn=_observing,
    ),
    VaonisSensorDescription(
        key="plan_state",
        translation_key="plan_state",
        value_fn=_plan_state,
        available_fn=_planning,
    ),
    VaonisSensorDescription(
        key="plan_target",
        translation_key="plan_target",
        value_fn=_plan_target,
        available_fn=_planning,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VaonisConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Stellina sensors."""
    coordinator = entry.runtime_data
    async_add_entities(VaonisSensor(coordinator, description) for description in SENSORS)


class VaonisSensor(VaonisEntity, SensorEntity):
    """A Stellina status sensor."""

    entity_description: VaonisSensorDescription

    def __init__(
        self, coordinator: VaonisCoordinator, description: VaonisSensorDescription
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the current value."""
        return self.entity_description.value_fn(self.coordinator)

    @property
    def available(self) -> bool:
        """Unavailable (rather than 'Unknown') when the value doesn't apply right now."""
        if not super().available:
            return False
        available_fn = self.entity_description.available_fn
        return available_fn is None or available_fn(self.coordinator)
