"""Native plan: body construction, scheduling, safety, and progress parsing."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from typing import Any

import pytest

from pyvaonis.client import VaonisClient
from pyvaonis.client import VaonisCommandError
from pyvaonis.models import VaonisStatus
from pyvaonis.plan import PlanItem
from pyvaonis.plan import PlanProgress
from pyvaonis.plan import build_plan

# A fixed night so solar-separation/ephemeris are deterministic.
WHEN = datetime(2026, 1, 15, 22, 0, tzinfo=UTC)
LAT, LON = 52.37, 4.90


def test_parse_item() -> None:
    assert PlanItem.parse("M42:30") == PlanItem("M42", 30.0)
    assert PlanItem.parse("Jupiter") == PlanItem("Jupiter", 20.0)


def test_build_plan_schedules_back_to_back_windows() -> None:
    body = build_plan(
        [PlanItem("M42", 30.0), PlanItem("M45", 20.0)],
        name="night",
        latitude=LAT,
        longitude=LON,
        device_id="me",
        start_time=WHEN,
    )
    payload = body.to_payload()
    assert payload["planName"] == "night"
    assert payload["deviceId"] == "me"
    assert payload["appVersion"]  # echoed
    assert len(payload["targets"]) == 2
    t0, t1 = payload["targets"]
    start_ms = int(WHEN.timestamp() * 1000)
    assert t0["startTime"] == start_ms
    assert t0["endTime"] == start_ms + 30 * 60_000
    # second target starts exactly when the first ends (no gap, no overlap)
    assert t1["startTime"] == t0["endTime"]
    assert t1["endTime"] == t1["startTime"] + 20 * 60_000
    assert t0["params"]["objectName"] == "Orion Nebula"


def test_build_plan_rejects_unknown_target() -> None:
    with pytest.raises(ValueError, match="unknown catalog object"):
        build_plan(
            [PlanItem("NotAThing", 10.0)],
            name="x",
            latitude=LAT,
            longitude=LON,
            device_id="me",
            start_time=WHEN,
        )


def test_build_plan_blocks_near_sun_target() -> None:
    # Build a body whose only target is the Sun's own catalog entry → must be refused.
    with pytest.raises(ValueError, match="Sun"):
        build_plan(
            [PlanItem("Sun", 10.0)],
            name="x",
            latitude=LAT,
            longitude=LON,
            device_id="me",
            start_time=WHEN,
        )


def _client(**status_fields: Any) -> VaonisClient:
    client = VaonisClient(ip="10.0.0.1", device_id="me")
    raw: dict[str, Any] = {
        "challenge": "xQUJD",
        "telescopeId": "T1",
        "bootCount": 1,
        "masterDeviceId": "me",
        "initialized": True,
    }
    raw.update(status_fields)
    client.status = VaonisStatus.model_validate(raw)
    return client


async def test_start_plan_requires_idle_and_posts_to_planner() -> None:
    busy = _client(currentOperation={"type": "OBSERVATION", "stopped": False})
    with pytest.raises(VaonisCommandError, match="already running"):
        await busy.start_plan([PlanItem("M42", 10.0)], latitude=LAT, longitude=LON)

    c = _client()
    seen: dict[str, Any] = {}

    async def fake_post(endpoint: str, body: Any = None, **k: Any) -> dict[str, Any]:
        seen["endpoint"] = endpoint
        seen["body"] = body
        return {"success": True}

    c.post = fake_post  # type: ignore[method-assign]
    await c.start_plan([PlanItem("M42", 10.0)], latitude=LAT, longitude=LON, start_time=WHEN)
    assert seen["endpoint"] == "planner/startPlan"
    assert seen["body"]["targets"][0]["params"]["objectId"] == "M42"


def test_plan_progress_parses_running_plan() -> None:
    raw = {
        "currentOperation": {
            "type": "PLAN",
            "stopped": False,
            "planName": "night",
            "state": "OBSERVATION",
            "targets": [
                {"storeState": "IDLE", "target": {"objectName": "Orion Nebula"}},
                {"storeState": "OBSERVING", "target": {"objectName": "Pleiades"}},
            ],
        }
    }
    prog = PlanProgress.from_status(raw)
    assert prog is not None
    assert prog.state == "OBSERVATION"
    assert prog.target_count == 2
    assert prog.current_index == 1
    assert prog.current_target == "Pleiades"
    assert not prog.finished

    # No plan running → None.
    assert PlanProgress.from_status({"currentOperation": {"type": "OBSERVATION"}}) is None
