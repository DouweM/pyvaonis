"""Human status composition from raw status, using the app's labels."""

from __future__ import annotations

from pyvaonis.labels import autoinit_step_label
from pyvaonis.labels import summarize


def test_idle() -> None:
    assert summarize(None) == "Idle"
    assert summarize({}) == "Idle"
    assert summarize({"currentOperation": {"type": "OBSERVATION", "stopped": True}}) == "Idle"
    assert summarize({"initialized": True}) == "Idle"


def test_not_initialized() -> None:
    assert summarize({"initialized": False}) == "Not initialized"
    # Idle but not yet aligned, even with a stopped op present.
    assert (
        summarize({"initialized": False, "currentOperation": {"type": "PARK", "stopped": True}})
        == "Not initialized"
    )


def test_observation_stacking() -> None:
    raw = {
        "currentOperation": {
            "type": "OBSERVATION",
            "target": {"objectName": "M104"},
            "capture": {
                "hasStacking": True,
                "stackingCount": 180,
                "cameraParams": {"exposureMicroSec": 10_000_000},
            },
        }
    }
    assert summarize(raw) == "M104: 180 stacked (30m)"


def test_observation_pointing_step() -> None:
    raw = {
        "currentOperation": {
            "type": "OBSERVATION",
            "target": {"objectName": "M51"},
            "capture": {"hasStacking": False},
            "steps": [{"type": "POINT_DEEP_SKY", "steps": [{"type": "GO_TARGET"}]}],
        }
    }
    assert summarize(raw) == "M51: Pointing at the target"


def test_autoinit_step_label_and_summary() -> None:
    raw = {
        "currentOperation": {
            "type": "AUTO_INIT",
            "steps": [{"type": "TRY_POSITION", "steps": [{"type": "ASTROMETRY", "progress": 0.5}]}],
        }
    }
    assert autoinit_step_label(raw) == "Star pattern analysis (50%)"
    assert summarize(raw) == "Initialization: Star pattern analysis (50%)"
    assert autoinit_step_label({"currentOperation": {"type": "OBSERVATION"}}) is None


def test_plan_summary_with_current_target() -> None:
    raw = {
        "currentOperation": {
            "type": "PLAN",
            "planName": "tonight",
            "state": "OBSERVATION",
            "targets": [
                {"storeState": "IDLE", "target": {"objectName": "M42"}},
                {"storeState": "OBSERVING", "target": {"objectName": "M51"}},
            ],
        }
    }
    assert summarize(raw) == "tonight — Observation in progress (M51, 2/2)"


def test_park_label() -> None:
    # The running-state wording, matching the app's badge (not the "Close the arm" button label).
    assert summarize({"currentOperation": {"type": "PARK"}}) == "Closing arm"


def test_sun_mode_state() -> None:
    assert summarize({"currentOperation": {"type": "SUN_MODE"}}) == "Solar mode"
    assert (
        summarize({"currentOperation": {"type": "SUN_MODE", "state": "CLOSING_ARM"}})
        == "Solar mode: Closing the arm"
    )


def test_autoinit_failure_not_enough_stars() -> None:
    from pyvaonis.labels import autoinit_failure

    raw = {
        "initialized": False,
        "currentOperation": None,
        "previousOperations": {
            "autoInit": {"id": "ai-1", "error": {"name": "GENERAL.ALL_ATTEMPTS_FAILED"}}
        },
    }
    failure = autoinit_failure(raw)
    assert failure is not None
    signature, short, detail = failure
    assert signature == "ai-1"
    assert short == "Not enough stars"
    assert "stars" in detail
    # summarize itself reflects the not-initialized scope; the sensor layer overlays the failure.
    assert summarize(raw) == "Not initialized"


def test_autoinit_failure_none_when_running_or_done() -> None:
    from pyvaonis.labels import autoinit_failure

    running = {"currentOperation": {"type": "AUTO_INIT", "stopped": False}}
    assert autoinit_failure(running) is None
    # A failed attempt but the scope is now initialized (a later init succeeded) → not a failure.
    done = {
        "initialized": True,
        "previousOperations": {"autoInit": {"error": {"name": "GENERAL.ALL_ATTEMPTS_FAILED"}}},
    }
    assert autoinit_failure(done) is None
    # User interruption is not flagged as a failure.
    interrupted = {
        "initialized": False,
        "previousOperations": {"autoInit": {"error": {"name": "GENERAL.MANUAL_INTERRUPTION"}}},
    }
    assert autoinit_failure(interrupted) is None
    assert autoinit_failure({}) is None
