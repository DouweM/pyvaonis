"""Run an unattended observing plan: a list of targets, each for a set duration, then stop.

Each target is a single observation that the telescope **live-stacks** for the requested
duration (integration grows with time), after which the sequencer moves to the next target.
It respects darkness (won't start/continue once the Sun rises above -10°) and can park and
shut the telescope down at the end — "follow these 5 things tonight and turn off".
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any

from . import astro

if TYPE_CHECKING:
    from .client import StellinaClient

EventCallback = Callable[["SequenceEvent"], Awaitable[None] | None]


@dataclass
class SequenceItem:
    """One step of a plan: observe ``target`` for ``minutes``."""

    target: str  # catalog id, designation, or name (e.g. "M42", "Jupiter")
    minutes: float

    @classmethod
    def parse(cls, spec: str) -> SequenceItem:
        """Parse ``"M42:30"`` (target:minutes; minutes default 20)."""
        target, _, minutes = spec.partition(":")
        return cls(target=target.strip(), minutes=float(minutes) if minutes else 20.0)


@dataclass
class SequenceEvent:
    """Progress notification emitted during a run."""

    kind: str  # waiting_for_dark | started | progress | finished | skipped | done
    item: SequenceItem | None = None
    detail: dict[str, Any] = field(default_factory=dict)


async def _emit(cb: EventCallback | None, event: SequenceEvent) -> None:
    if cb is None:
        return
    result = cb(event)
    if asyncio.iscoroutine(result):
        await result


async def run_sequence(
    client: StellinaClient,
    items: list[SequenceItem],
    *,
    latitude: float,
    longitude: float,
    require_dark: bool = True,
    wait_for_dark: bool = False,
    park_at_end: bool = True,
    shutdown_at_end: bool = False,
    poll_interval: float = 30.0,
    on_event: EventCallback | None = None,
) -> None:
    """Execute an observing plan. See module docstring for behaviour."""

    def dark() -> bool:
        return astro.is_dark(latitude, longitude)

    if wait_for_dark:
        while require_dark and not dark():
            await _emit(on_event, SequenceEvent("waiting_for_dark"))
            await asyncio.sleep(poll_interval)

    await client.take_control()
    try:
        for item in items:
            if require_dark and not dark():
                await _emit(on_event, SequenceEvent("skipped", item, {"reason": "no longer dark"}))
                break
            try:
                await client.observe_object(item.target)
            except Exception as err:
                await _emit(on_event, SequenceEvent("skipped", item, {"error": str(err)}))
                continue
            await _emit(on_event, SequenceEvent("started", item))

            deadline = time.monotonic() + item.minutes * 60
            while time.monotonic() < deadline:
                await asyncio.sleep(min(poll_interval, max(1.0, deadline - time.monotonic())))
                obs = client.current_observation()
                if obs is not None:
                    await _emit(
                        on_event,
                        SequenceEvent(
                            "progress",
                            item,
                            {
                                "stacking_count": obs.stacking_count,
                                "integration_seconds": obs.integration_seconds,
                                "step": obs.current_step,
                            },
                        ),
                    )
                if require_dark and not dark():
                    break
            await _emit(on_event, SequenceEvent("finished", item))
    finally:
        # Tear down in a safe order, waiting for each step to actually complete so we never
        # park mid-slew or cut power mid-operation.
        with contextlib.suppress(Exception):  # may already be idle
            await client.stop_observation()
        await _wait_idle(client, poll_interval)
        if park_at_end:
            try:
                await client.park()
                await _wait_idle(client, poll_interval)
            except Exception as err:
                await _emit(on_event, SequenceEvent("skipped", detail={"park_error": str(err)}))
        if shutdown_at_end:
            await client.request_shutdown(force=True)
    await _emit(on_event, SequenceEvent("done", detail={"shutdown": shutdown_at_end}))


async def _wait_idle(
    client: StellinaClient, poll_interval: float, *, timeout: float = 180.0
) -> bool:
    """Wait until no operation is running (so park/shutdown are safe). Returns False on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.status
        if status is None or not status.is_busy:
            return True
        await asyncio.sleep(min(poll_interval, max(1.0, deadline - time.monotonic())))
    return False
