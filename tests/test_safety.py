"""Safety guards: destructive/firmware/solar blocking and command preconditions."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from typing import Any

import pytest

from pystellina import ObservationBody
from pystellina import StellinaClient
from pystellina import astro
from pystellina.client import StellinaCommandError
from pystellina.models import StellinaStatus


def _client(**status_fields: Any) -> StellinaClient:
    client = StellinaClient(ip="10.0.0.1", device_id="me")
    raw: dict[str, Any] = {
        "challenge": "xQUJD",
        "telescopeId": "T1",
        "bootCount": 1,
        "masterDeviceId": "me",  # we hold control by default
        "initialized": True,
    }
    raw.update(status_fields)
    client.status = StellinaStatus.model_validate(raw)
    return client


# -- endpoint guards (static) ----------------------------------------------------------
def test_firmware_upload_always_blocked() -> None:
    c = _client()
    with pytest.raises(StellinaCommandError, match="firmware"):
        c._guard_endpoint("updates/uploadUpdateFile", allow_unsafe=True)


def test_destructive_blocked_without_optin() -> None:
    c = _client()
    for ep in ("storage/deleteUserStorageFolders", "userManager/applyResetResponse"):
        with pytest.raises(StellinaCommandError):
            c._guard_endpoint(ep, allow_unsafe=False)
        c._guard_endpoint(ep, allow_unsafe=True)  # explicit opt-in allowed


def test_solar_blocked_without_optin() -> None:
    c = _client()
    with pytest.raises(StellinaCommandError, match="solar"):
        c._guard_endpoint("sun/startSunMode", allow_unsafe=False)


def test_safe_endpoints_pass() -> None:
    c = _client()
    c._guard_endpoint("general/park", allow_unsafe=False)
    c._guard_endpoint("app/status", allow_unsafe=False)


# -- preconditions ---------------------------------------------------------------------
def test_require_control_fails_when_not_master() -> None:
    c = _client(masterDeviceId="someone-else")
    with pytest.raises(StellinaCommandError, match="control"):
        c._require_control("x")


async def test_park_refused_when_busy() -> None:
    c = _client(currentOperation={"type": "OBSERVATION", "stopped": False})
    posted: list[Any] = []
    c.post = lambda *a, **k: posted.append(a)  # type: ignore[method-assign]
    with pytest.raises(StellinaCommandError, match="operation is already running"):
        await c.park()
    assert not posted  # guard fired before any POST


async def test_shutdown_refused_when_busy_without_force() -> None:
    c = _client(currentOperation={"type": "OBSERVATION", "stopped": False})
    with pytest.raises(StellinaCommandError):
        await c.request_shutdown()


async def test_switch_frequency_validates_band() -> None:
    c = _client()
    with pytest.raises(StellinaCommandError, match="band"):
        await c.switch_frequency("BAND_60_GHZ")


async def test_observe_refused_when_uninitialized() -> None:
    c = _client(initialized=False)
    with pytest.raises(StellinaCommandError, match="initialised"):
        await c.start_observation(ObservationBody(object_name="x", ra=10.0, de=20.0))


async def test_observe_refused_when_busy_without_replace() -> None:
    c = _client(currentOperation={"type": "OBSERVATION", "stopped": False})
    with pytest.raises(StellinaCommandError, match="already running"):
        await c.start_observation(ObservationBody(object_name="x", ra=10.0, de=20.0))


async def test_observe_replace_stops_running_observation_then_starts() -> None:
    c = _client(currentOperation={"type": "OBSERVATION", "stopped": False})
    seen: list[str] = []

    async def fake_post(endpoint: str, body: Any = None, **k: Any) -> dict[str, Any]:
        seen.append(endpoint)
        return {"success": True}

    async def fake_wait_idle(*, timeout: float = 30.0) -> None:
        # The firmware would clear currentOperation; simulate that so the start guard passes.
        c.status = _client().status  # idle, initialised, we hold control

    c.post = fake_post  # type: ignore[method-assign]
    c._wait_idle = fake_wait_idle  # type: ignore[method-assign]

    sun_ra, sun_dec = astro.sun_position(datetime.now(UTC))
    await c.start_observation(
        ObservationBody(object_name="anti-sun", ra=(sun_ra + 180.0) % 360.0, de=-sun_dec),
        replace=True,
    )
    assert seen == ["general/stopObservation", "general/startObservation"]


async def test_observe_blocks_near_sun() -> None:
    c = _client()
    sun_ra, sun_dec = astro.sun_position(datetime.now(UTC))
    with pytest.raises(StellinaCommandError, match="Sun"):
        await c.start_observation(ObservationBody(object_name="too close", ra=sun_ra, de=sun_dec))


async def test_in_observation_actions_require_control_and_build_bodies() -> None:
    blocked = _client(masterDeviceId="someone-else")
    with pytest.raises(StellinaCommandError, match="control"):
        await blocked.adjust_framing(1, 2)

    c = _client(currentOperation={"type": "OBSERVATION", "stopped": False})  # master, observing
    seen: dict[str, Any] = {}

    async def fake_post(endpoint: str, body: Any = None, **k: Any) -> dict[str, Any]:
        seen["endpoint"] = endpoint
        seen["body"] = body
        return {"success": True}

    c.post = fake_post  # type: ignore[method-assign]

    await c.adjust_framing(5, -3, 1.5)
    assert seen == {
        "endpoint": "general/adjustObservationFraming",
        "body": {"x": 5, "y": -3, "rot": 1.5},
    }

    await c.restart_autofocus()
    assert seen["endpoint"] == "general/adjustObservationFocus"
    assert seen["body"] == {"restartCapture": True}

    await c.set_multi_light(True)
    assert seen["endpoint"] == "app/setSettings"
    # no other settings in the test status, plus the two non-nullable SettingsBody defaults
    assert seen["body"] == {
        "enableHdrBackground": True,
        "buttonBrightness": "MEDIUM",
        "algoHdrBackground": "RECOMMENDED",
    }

    await c.enable_multi_night()
    assert seen["endpoint"] == "capture/setToBeResumable"


async def test_observe_allows_far_from_sun_and_sends_full_body() -> None:
    c = _client()
    captured: dict[str, Any] = {}

    async def fake_post(endpoint: str, body: Any = None, **k: Any) -> dict[str, Any]:
        captured["endpoint"] = endpoint
        captured["body"] = body
        return {"success": True}

    c.post = fake_post  # type: ignore[method-assign]
    sun_ra, sun_dec = astro.sun_position(datetime.now(UTC))
    await c.start_observation(
        ObservationBody(object_name="anti-sun", ra=(sun_ra + 180.0) % 360.0, de=-sun_dec)
    )
    assert captured["endpoint"] == "general/startObservation"
    assert captured["body"]["observationType"] == "STANDARD"  # app-required field is sent
    assert captured["body"]["algorithm"] == "AUTO"  # the app sends AUTO for non-manual observations
