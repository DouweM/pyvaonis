"""Native "Plan My Night": build and track the telescope's own autonomous observing plan.

Unlike a client-driven loop, a plan is uploaded once (``planner/startPlan``) and the **firmware**
runs the whole night by itself — auto-initialising, slewing target-to-target on a schedule, and
advancing when each target's time window ends. It therefore keeps going even if the controlling
client disconnects. This module turns a simple ``["M42:30", "M51:20"]`` target list into a valid
:class:`~pystellina.models.PlanBody` (assigning each target a back-to-back time window) and parses
the plan's live progress out of the status stream.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from . import astro
from . import const
from .catalog import get_object
from .models import PlanBody
from .models import PlanTargetBody

# Firmware plan states (StellinaPlanOperation), in rough lifecycle order.
PLAN_STATE_FINISHED = "FINISHED"


@dataclass
class PlanItem:
    """One target of a plan: observe ``target`` for ``minutes``."""

    target: str  # catalog id, designation, or name (e.g. "M42", "Jupiter")
    minutes: float

    @classmethod
    def parse(cls, spec: str) -> PlanItem:
        """Parse ``"M42:30"`` (target:minutes; minutes default 20)."""
        target, _, minutes = spec.partition(":")
        return cls(target=target.strip(), minutes=float(minutes) if minutes else 20.0)


def build_plan(
    items: list[PlanItem],
    *,
    name: str,
    latitude: float,
    longitude: float,
    device_id: str,
    start_time: datetime | None = None,
    observatory_name: str = "pystellina",
    app_version: str = const.APP_VERSION,
    allow_solar: bool = False,
) -> PlanBody:
    """Turn a target list into a :class:`PlanBody` with back-to-back time windows.

    Each item gets ``[cursor, cursor + minutes]`` (epoch ms) starting at ``start_time`` (default
    now); the firmware waits until the first window opens, so pass a dusk time to defer the run.
    Solar objects are resolved to ephemeris coordinates at their window start, and near-Sun targets
    are refused unless ``allow_solar`` (sensor-damage guard, as for single observations).
    """
    if not items:
        raise ValueError("a plan needs at least one target")
    start = start_time or datetime.now(UTC)
    cursor = int(start.timestamp() * 1000)

    targets: list[PlanTargetBody] = []
    for item in items:
        obj = get_object(item.target)
        if obj is None:
            raise ValueError(f"unknown catalog object: {item.target!r}")
        when = datetime.fromtimestamp(cursor / 1000, UTC)
        try:
            params = obj.to_observation(when=when)
            # Solar bodies carry no ra/de, so resolve coords from the object for the near-Sun guard.
            check_ra, check_de = params.ra, params.de
            if check_ra is None and obj.is_solar:
                check_ra, check_de = obj.coordinates(when)
        except RuntimeError as err:  # ephem missing for a solar object
            raise ValueError(str(err)) from err
        if not allow_solar and check_ra is not None and check_de is not None:
            sep = astro.separation_from_sun(check_ra, check_de, when)
            if sep < const.SOLAR_EXCLUSION_DEG:
                raise ValueError(
                    f"{obj.display_name} is {sep:.1f}° from the Sun at its window "
                    f"(<{const.SOLAR_EXCLUSION_DEG}°); refused (set allow_solar=True only with the "
                    "Vaonis solar filter installed)."
                )
        dur_ms = int(item.minutes * 60_000)
        targets.append(PlanTargetBody(start_time=cursor, end_time=cursor + dur_ms, params=params))
        cursor += dur_ms

    return PlanBody(
        plan_id=str(uuid.uuid4()),
        plan_version=str(uuid.uuid4()),
        plan_name=name,
        targets=targets,
        latitude=latitude,
        longitude=longitude,
        device_id=device_id,
        app_version=app_version,
        observatory_name=observatory_name,
    )


class PlanProgress(BaseModel):
    """A compact view of the running native plan, derived from status."""

    plan_name: str | None = None
    state: str | None = None  # WAITING_UNTIL_START | AUTO_INIT | OBSERVATION | FINISHED | ...
    target_count: int = 0
    current_index: int | None = None  # 0-based index of the target being observed
    current_target: str | None = None
    finished: bool = False
    stopped: bool = False

    @classmethod
    def from_status(cls, status_raw: dict[str, Any]) -> PlanProgress | None:
        """Parse the current plan from a raw status dict, or None if no plan is running."""
        op = status_raw.get("currentOperation")
        if not (isinstance(op, dict) and op.get("type") == "PLAN" and not op.get("stopped")):
            return None
        targets = [t for t in (op.get("targets") or []) if isinstance(t, dict)]
        current_index: int | None = None
        for i, tgt in enumerate(targets):
            if tgt.get("storeState") == "OBSERVING":
                current_index = i
                break
        current_target = None
        if current_index is not None:
            tgt = (targets[current_index].get("target") or {}) if targets else {}
            current_target = tgt.get("objectName") or tgt.get("objectId")
        return cls(
            plan_name=op.get("planName"),
            state=op.get("state"),
            target_count=len(targets),
            current_index=current_index,
            current_target=current_target,
            finished=op.get("state") == PLAN_STATE_FINISHED,
            stopped=bool(op.get("stopped")),
        )
