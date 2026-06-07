"""Number platform: the mosaic field size (degrees) the Observe button uses when Mosaic is on.

These are UI inputs, not device state — they hold the chosen field size locally (restored across
restarts) and write it onto the coordinator so the Observe button / observe service can apply it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import NumberEntityDescription
from homeassistant.components.number import NumberMode
from homeassistant.components.number import RestoreNumber
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import VaonisConfigEntry
from .coordinator import VaonisCoordinator
from .entity import VaonisEntity


@dataclass(frozen=True, kw_only=True)
class VaonisNumberDescription(NumberEntityDescription):
    """Number bound to a coordinator attribute (the value the Observe button reads)."""

    get_fn: Callable[[VaonisCoordinator], float]
    set_fn: Callable[[VaonisCoordinator, float], None]


NUMBERS: tuple[VaonisNumberDescription, ...] = (
    VaonisNumberDescription(
        key="mosaic_width",
        translation_key="mosaic_width",
        icon="mdi:arrow-expand-horizontal",
        native_min_value=0.5,
        native_max_value=6.0,
        native_step=0.1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        get_fn=lambda c: c.mosaic_width,
        set_fn=lambda c, v: setattr(c, "mosaic_width", v),
    ),
    VaonisNumberDescription(
        key="mosaic_height",
        translation_key="mosaic_height",
        icon="mdi:arrow-expand-vertical",
        native_min_value=0.5,
        native_max_value=6.0,
        native_step=0.1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        get_fn=lambda c: c.mosaic_height,
        set_fn=lambda c, v: setattr(c, "mosaic_height", v),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VaonisConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the mosaic field-size numbers."""
    coordinator = entry.runtime_data
    async_add_entities(VaonisNumber(coordinator, d) for d in NUMBERS)


class VaonisNumber(VaonisEntity, RestoreNumber):
    """A mosaic field-size input (degrees), persisted across restarts."""

    entity_description: VaonisNumberDescription
    _attr_native_unit_of_measurement = "°"

    def __init__(
        self, coordinator: VaonisCoordinator, description: VaonisNumberDescription
    ) -> None:
        """Initialise the number entity."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        """Restore the last-set value into the coordinator."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_number_data()) is not None and (
            last.native_value is not None
        ):
            self.entity_description.set_fn(self.coordinator, float(last.native_value))

    @property
    def native_value(self) -> float:
        """The current field size in degrees."""
        return self.entity_description.get_fn(self.coordinator)

    @property
    def available(self) -> bool:
        """Greyed out only while busy — mosaic size applies to a new observation (set at start). It's
        a local input the Observe button reads, so it stays settable while the scope is offline."""
        data = self.coordinator.data
        return not (data and data.is_busy)

    async def async_set_native_value(self, value: float) -> None:
        """Store the new field size (used by the Observe button when Mosaic is on)."""
        self.entity_description.set_fn(self.coordinator, value)
        self.async_write_ha_state()
