"""Button platform for the Stellina integration (one-shot commands)."""

from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity
from homeassistant.components.button import ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from pyvaonis import VaonisClient
from pyvaonis import VaonisError

from .coordinator import VaonisConfigEntry
from .coordinator import VaonisCoordinator
from .entity import VaonisEntity


@dataclass(frozen=True, kw_only=True)
class VaonisButtonDescription(ButtonEntityDescription):
    """Button description bound to a client coroutine."""

    press_fn: Callable[[VaonisClient], Awaitable[object]]
    # Most actions need control; take it on demand first. False for the control buttons themselves.
    takes_control: bool = True


BUTTONS: tuple[VaonisButtonDescription, ...] = (
    VaonisButtonDescription(
        key="take_control",
        translation_key="take_control",
        press_fn=lambda client: client.take_control(),
        takes_control=False,
    ),
    VaonisButtonDescription(
        key="park",
        translation_key="park",
        press_fn=lambda client: client.park(),
    ),
    VaonisButtonDescription(
        key="stop",
        translation_key="stop",
        press_fn=lambda client: client.stop_observation(),
    ),
    VaonisButtonDescription(
        key="shutdown",
        translation_key="shutdown",
        press_fn=lambda client: client.request_shutdown(),
    ),
    VaonisButtonDescription(
        key="release_control",
        translation_key="release_control",
        press_fn=lambda client: client.release_control(),
        takes_control=False,
    ),
    VaonisButtonDescription(
        key="restart_autofocus",
        translation_key="restart_autofocus",
        press_fn=lambda client: client.restart_autofocus(),
    ),
    VaonisButtonDescription(
        key="enable_multi_night",
        translation_key="enable_multi_night",
        press_fn=lambda client: client.enable_multi_night(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VaonisConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Stellina buttons."""
    coordinator = entry.runtime_data
    async_add_entities(VaonisButton(coordinator, description) for description in BUTTONS)


class VaonisButton(VaonisEntity, ButtonEntity):
    """A Stellina command button."""

    entity_description: VaonisButtonDescription

    def __init__(
        self, coordinator: VaonisCoordinator, description: VaonisButtonDescription
    ) -> None:
        """Initialise the button."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Execute the command — surfacing errors cleanly.

        Control-needing actions are one-shot: take control, act, release (so the phone can resume).
        The take/release-control buttons are the manual exception (they hold/drop control directly).
        """
        press_fn = self.entity_description.press_fn
        try:
            if self.entity_description.takes_control:
                await self.coordinator.run_action(press_fn)
            else:
                await press_fn(self.coordinator.client)
        except VaonisError as err:
            raise HomeAssistantError(str(err)) from err
