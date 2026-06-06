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

from .coordinator import VaonisConfigEntry
from .coordinator import VaonisCoordinator
from .entity import VaonisEntity
from .pyvaonis import VaonisClient
from .pyvaonis import VaonisError


@dataclass(frozen=True, kw_only=True)
class VaonisButtonDescription(ButtonEntityDescription):
    """Button description bound to a client coroutine."""

    press_fn: Callable[[VaonisClient], Awaitable[object]]
    # Most actions need control; take it on demand first. False for the control buttons themselves.
    takes_control: bool = True
    # When set, the button is disabled (greyed out) unless this returns True for the current state.
    available_fn: Callable[[VaonisCoordinator], bool] | None = None


def _observing(c: VaonisCoordinator) -> bool:
    """An observation is running (mirrors the app's ``operationRunning``)."""
    return c.client.current_observation() is not None


def _idle(c: VaonisCoordinator) -> bool:
    """No operation of any kind is running (the app's ``currentOperation == null``)."""
    return bool(c.data) and not c.data.is_busy


def _nobody_in_control(c: VaonisCoordinator) -> bool:
    """No device currently holds master (the app's ``masterDeviceId == null``)."""
    return bool(c.data) and not c.data.master_device_id


def _is_parked(c: VaonisCoordinator) -> bool:
    """Whether the arm is parked/closed (the app's ``isParked`` over the ALT motor)."""
    motors = (c.data.raw.get("motors") if c.data else None) or {}
    alt = motors.get("ALT") or {}
    return bool(alt.get("atStop")) or alt.get("calibrated") is False


BUTTONS: tuple[VaonisButtonDescription, ...] = (
    VaonisButtonDescription(
        key="take_control",
        translation_key="take_control",
        icon="mdi:remote",
        press_fn=lambda client: client.take_control(),
        takes_control=False,
        # App gate: take control only when nobody holds it (can't steal a master device).
        available_fn=_nobody_in_control,
    ),
    VaonisButtonDescription(
        key="park",
        translation_key="park",
        icon="mdi:home-import-outline",
        press_fn=lambda client: client.park(),
        # App gate: park only when idle and not already parked.
        available_fn=lambda c: _idle(c) and not _is_parked(c),
    ),
    VaonisButtonDescription(
        key="stop",
        translation_key="stop",
        icon="mdi:stop",
        press_fn=lambda client: client.stop_observation(),
        # App's Stop covers observations; a native plan is cancelled via the stop_plan service.
        available_fn=_observing,
    ),
    VaonisButtonDescription(
        key="shutdown",
        translation_key="shutdown",
        icon="mdi:power",
        press_fn=lambda client: client.request_shutdown(),
    ),
    VaonisButtonDescription(
        key="release_control",
        translation_key="release_control",
        icon="mdi:remote-off",
        press_fn=lambda client: client.release_control(),
        takes_control=False,
        available_fn=lambda c: c.client.has_control,
    ),
    VaonisButtonDescription(
        key="restart_autofocus",
        translation_key="restart_autofocus",
        icon="mdi:image-filter-center-focus",
        press_fn=lambda client: client.restart_autofocus(),
        available_fn=_observing,
    ),
    # (multi-night is the "Multi-night mode" switch — it applies to the running capture too.)
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VaonisConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Stellina buttons."""
    coordinator = entry.runtime_data
    entities: list[ButtonEntity] = [VaonisButton(coordinator, d) for d in BUTTONS]
    entities.append(VaonisInitializeButton(coordinator))
    entities.append(VaonisObserveButton(coordinator))
    entities.append(VaonisResumeButton(coordinator))
    async_add_entities(entities)


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

    @property
    def available(self) -> bool:
        """Greyed out when the command doesn't apply (e.g. release control without control)."""
        if not super().available:
            return False
        available_fn = self.entity_description.available_fn
        return available_fn is None or available_fn(self.coordinator)


class VaonisInitializeButton(VaonisEntity, ButtonEntity):
    """Initialise/align the telescope (plate-solve + autofocus) — the core "Initialize" action.

    Its own class because it resolves a location: the telescope's last-known position, then Home
    Assistant's configured home (the scope has no GPS, so home covers a first init). Mirrors the
    ``vaonis.autoinit`` service; available only when idle (the app's ``canStartOperation``).
    """

    _attr_translation_key = "initialize"
    _attr_icon = "mdi:crosshairs-gps"

    def __init__(self, coordinator: VaonisCoordinator) -> None:
        """Initialise the button."""
        super().__init__(coordinator, "initialize")

    async def async_press(self) -> None:
        """Take control and run auto-init at the resolved location."""
        scope = self.coordinator.client.location()
        lat = scope[0] if scope else self.hass.config.latitude
        lon = scope[1] if scope else self.hass.config.longitude
        try:
            await self.coordinator.run_action(lambda c: c.start_autoinit(lat, lon))
        except VaonisError as err:
            raise HomeAssistantError(str(err)) from err

    @property
    def available(self) -> bool:
        """Available only when idle (no operation running)."""
        return (
            super().available and bool(self.coordinator.data) and not self.coordinator.data.is_busy
        )


class VaonisObserveButton(VaonisEntity, ButtonEntity):
    """Start observing the target chosen in the "Tonight's target" select.

    The select only picks the target (no movement); this button starts it — the app's
    browse-then-Observe flow. Available only when idle, initialized, and a target is chosen
    (the app's ``canStartObservation``).
    """

    _attr_translation_key = "observe"
    _attr_icon = "mdi:play"

    def __init__(self, coordinator: VaonisCoordinator) -> None:
        """Initialise the button."""
        super().__init__(coordinator, "observe")

    async def async_press(self) -> None:
        """Take control and start observing the selected target.

        Applies the Mosaic / Multi-night toggles (and the mosaic width/height numbers) if they're on.
        """
        c = self.coordinator
        target = c.selected_target
        if not target:
            raise HomeAssistantError("No target selected — pick one in 'Tonight's target' first")
        mosaic = (c.mosaic_width, c.mosaic_height) if c.mosaic_enabled else None
        try:
            await c.run_action(
                lambda cl: cl.observe_object(
                    target, replace=True, mosaic=mosaic, multi_night=c.multi_night_enabled
                )
            )
        except VaonisError as err:
            raise HomeAssistantError(str(err)) from err

    @property
    def available(self) -> bool:
        """Enabled when idle, initialized, and a target has been chosen."""
        data = self.coordinator.data
        return (
            super().available
            and bool(data)
            and not data.is_busy
            and bool(data.initialized)
            and bool(self.coordinator.selected_target)
        )


class VaonisResumeButton(VaonisEntity, ButtonEntity):
    """Resume the most recent saved multi-night capture, continuing its stack.

    Enabled when idle, initialized, and at least one capture is saved. For a specific one, use the
    ``vaonis.resume`` service with a ``store_id`` (see the Multi-night captures sensor).
    """

    _attr_translation_key = "resume"
    _attr_icon = "mdi:play-box-multiple"

    def __init__(self, coordinator: VaonisCoordinator) -> None:
        """Initialise the resume button."""
        super().__init__(coordinator, "resume")

    async def async_press(self) -> None:
        """Resume the newest saved capture (highest, date-prefixed storeId)."""
        saved = self.coordinator.client.stored_captures()
        if not saved:
            raise HomeAssistantError("No saved multi-night captures to resume")
        newest = max(saved, key=lambda c: c.get("storeId") or "")
        store_id = newest.get("storeId")
        try:
            await self.coordinator.run_action(lambda c: c.resume_capture(store_id, replace=True))
        except VaonisError as err:
            raise HomeAssistantError(str(err)) from err

    @property
    def available(self) -> bool:
        """Enabled when idle, initialized, and there's a saved capture to resume."""
        data = self.coordinator.data
        return (
            super().available
            and bool(data)
            and not data.is_busy
            and bool(data.initialized)
            and bool(self.coordinator.client.stored_captures())
        )
