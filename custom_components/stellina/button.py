"""Button platform for the Stellina integration (one-shot commands)."""

from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity
from homeassistant.components.button import ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from pystellina import StellinaClient

from .coordinator import StellinaConfigEntry
from .coordinator import StellinaCoordinator
from .entity import StellinaEntity


@dataclass(frozen=True, kw_only=True)
class StellinaButtonDescription(ButtonEntityDescription):
    """Button description bound to a client coroutine."""

    press_fn: Callable[[StellinaClient], Awaitable[object]]


BUTTONS: tuple[StellinaButtonDescription, ...] = (
    StellinaButtonDescription(
        key="take_control",
        translation_key="take_control",
        press_fn=lambda client: client.take_control(),
    ),
    StellinaButtonDescription(
        key="park",
        translation_key="park",
        press_fn=lambda client: client.park(),
    ),
    StellinaButtonDescription(
        key="stop",
        translation_key="stop",
        press_fn=lambda client: client.stop_observation(),
    ),
    StellinaButtonDescription(
        key="shutdown",
        translation_key="shutdown",
        press_fn=lambda client: client.request_shutdown(),
    ),
    StellinaButtonDescription(
        key="release_control",
        translation_key="release_control",
        press_fn=lambda client: client.release_control(),
    ),
    StellinaButtonDescription(
        key="restart_autofocus",
        translation_key="restart_autofocus",
        press_fn=lambda client: client.restart_autofocus(),
    ),
    StellinaButtonDescription(
        key="enable_multi_night",
        translation_key="enable_multi_night",
        press_fn=lambda client: client.enable_multi_night(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StellinaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Stellina buttons."""
    coordinator = entry.runtime_data
    async_add_entities(StellinaButton(coordinator, description) for description in BUTTONS)


class StellinaButton(StellinaEntity, ButtonEntity):
    """A Stellina command button."""

    entity_description: StellinaButtonDescription

    def __init__(
        self, coordinator: StellinaCoordinator, description: StellinaButtonDescription
    ) -> None:
        """Initialise the button."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Execute the command."""
        await self.entity_description.press_fn(self.coordinator.client)
