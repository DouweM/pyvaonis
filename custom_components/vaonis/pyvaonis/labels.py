"""Human-readable status, using the Singularity app's own English labels.

The telescope's status carries raw enum constants (``OBSERVATION``, ``OPEN_ARM``,
``WAITING_UNTIL_START``, …). The app maps these to localized strings before showing them; this
module reproduces the English ones (extracted from ``res/values/strings.xml`` /
``StellinaStatusExtension``) so the CLI and Home Assistant can present the same wording the app does.
:func:`summarize` composes the one-line headline the app shows ("what is the scope doing right now").
"""

from __future__ import annotations

from typing import Any

# StellinaAutoInitStepType -> label (initialization_steps_*). TRY_POSITION is an outer container.
AUTOINIT_STEP_LABELS: dict[str, str] = {
    "PREPARE_MC_BOARD": "Start",
    "SEEK_STOP": "Start",
    "OPEN_ARM": "Star zone search",
    "MOVING": "Star zone search",
    "ASTROMETRY": "Star pattern analysis",
    "AUTO_FOCUS": "Autofocus",
    "START_TRACKING": "Tracking activation",
    "WIDE_AUTOFOCUS_NO_FACTORY": "First autofocus",
    "WIDE_AUTOFOCUS_FAILED_ASTROMETRY": "Focus correction",
    "RETRY_ASTROMETRY": "New star pattern analysis",
}

# StellinaPlanOperationState -> label (plan_state_*).
PLAN_STATE_LABELS: dict[str, str] = {
    "WAITING_UNTIL_START": "Standby",
    "AUTO_INIT": "Initialization in progress",
    "WAITING_FOR_OBSERVATION_START": "Waiting for next observation",
    "WAITING_FOR_RETRY": "Waiting for next attempt",
    "OBSERVATION": "Observation in progress",
    "FINISHED": "Finished",
}

# StellinaObservationStepType -> label (observation_steps_*).
OBS_STEP_LABELS: dict[str, str] = {
    "POINT_DEEP_SKY": "Pointing at the target",
    "POINT_BRIGHT_ZONE": "Pointing at the target",
    "POINT_TARGET": "Pointing at the target",
    "POINT_AT_OFFSET": "Pointing near target",
    "POINT_AT_SECOND_OFFSET": "Pointing near target",
    "GO_TARGET": "Pointing at the target",
    "GO_RELATIVE": "Pointing at the target",
    "START_TRACKING": "Tracking activation",
    "ASTROMETRY": "Checking position accuracy",
    "AUTO_FOCUS": "Autofocus",
    "CAPTURE": "Capture starting",
    "SEEK_STOP_ALT": "Resetting arm position",
    "POINTING_SUN": "Looking for the Sun",
    "CENTERING": "Centering the Sun",
}

# StellinaError.name (on previousOperations.autoInit.error) -> friendly reason (stellinaErrors_*).
# The app shows these on the "Initialization failed" screen; ALL_ATTEMPTS_FAILED is the not-enough-
# stars case. MANUAL_INTERRUPTION (the user stopped it) is deliberately not treated as a failure.
AUTOINIT_ERROR_LABELS: dict[str, str] = {
    "GENERAL.ALL_ATTEMPTS_FAILED": (
        "Couldn't find enough stars to determine position — needs clearer skies"
    ),
    "GENERAL.AUTO_FOCUS_FAILED": "Autofocus failed during initialization",
}
# Terse version for the one-line status headline (the full text above would make it unwieldy).
# Capitalised to match the other post-colon status bits (e.g. "Initialization: Star pattern analysis").
AUTOINIT_ERROR_SHORT: dict[str, str] = {
    "GENERAL.ALL_ATTEMPTS_FAILED": "Not enough stars",
    "GENERAL.AUTO_FOCUS_FAILED": "Autofocus failed",
}

# StellinaOperationType -> banner label (instrument_* / *_title).
OPERATION_TYPE_LABELS: dict[str, str] = {
    "AUTO_INIT": "Initialization",
    "OBSERVATION": "Observation in progress",
    "PARK": "Close the arm",
    "PLAN": "Plan in progress",
    "GENERATE_DARK": "Generating dark frames",
    "SUN_MODE": "Solar mode",
    "SUN_OBSERVATION": "Solar mode",
    "STORAGE_ACQUISITION": "Raw acquisition",
    "PLAYLIST": "Playlist in progress",
}


def _current_step(steps: Any) -> tuple[str | None, float | None]:
    """Walk a status ``steps`` tree to the active leaf, returning (type, progress 0-1)."""
    if not isinstance(steps, list) or not steps:
        return None, None
    cur = steps[-1] if isinstance(steps[-1], dict) else {}
    nested = cur.get("steps")
    if isinstance(nested, list) and nested:
        return _current_step(nested)
    return cur.get("type"), cur.get("progress")


def _pct(progress: float | None) -> str:
    return f" ({progress * 100:.0f}%)" if isinstance(progress, int | float) else ""


def _duration(seconds: float | None) -> str | None:
    if not seconds:
        return None
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"


def autoinit_step_label(raw: dict[str, Any] | None) -> str | None:
    """Label for the current auto-init step, or None when not initialising."""
    op = raw.get("currentOperation") if raw else None
    if not (isinstance(op, dict) and op.get("type") == "AUTO_INIT" and not op.get("stopped")):
        return None
    step_type, progress = _current_step(op.get("steps"))
    label = AUTOINIT_STEP_LABELS.get(step_type or "", "Initializing")
    return label + _pct(progress)


def autoinit_failure(raw: dict[str, Any] | None) -> tuple[str, str | None, str] | None:
    """The last auto-init failure as ``(signature, short, detail)``, or None if it didn't fail.

    When init fails (e.g. not enough stars) the firmware clears ``currentOperation`` and leaves the
    attempt in ``previousOperations.autoInit`` with a non-null ``error``, while ``initialized`` stays
    false. We surface that (which the status headline would otherwise show as plain "Idle"). ``short``
    is a terse reason for the one-line status (None when unrecognised); ``detail`` is the full message.
    ``signature`` (the attempt's id/endTime) lets callers dismiss one specific failure. Returns None
    while an init is running, after a successful init, once initialized, or for a user interruption.
    """
    if not raw or raw.get("initialized") is True:
        return None
    cur = raw.get("currentOperation")
    if isinstance(cur, dict) and cur.get("type") == "AUTO_INIT" and not cur.get("stopped"):
        return None  # an init is currently in progress
    prev = (raw.get("previousOperations") or {}).get("autoInit")
    if not isinstance(prev, dict) or not isinstance(prev.get("error"), dict):
        return None
    name = prev["error"].get("name") or ""
    if name == "GENERAL.MANUAL_INTERRUPTION":  # the user stopped it — not a failure to flag
        return None
    detail = (
        AUTOINIT_ERROR_LABELS.get(name) or prev["error"].get("rawError") or "Initialization failed"
    )
    signature = str(prev.get("id") or prev.get("endTime") or name or "failed")
    return signature, AUTOINIT_ERROR_SHORT.get(name), detail


def summarize(raw: dict[str, Any] | None) -> str:
    """One-line "what is the scope doing right now", in the app's wording."""
    op = raw.get("currentOperation") if raw else None
    if not isinstance(op, dict) or op.get("stopped"):
        return "Idle"
    op_type = op.get("type")

    if op_type == "OBSERVATION":
        target = (op.get("target") or {}).get("objectName") or "target"
        capture = op.get("capture") or {}
        count = capture.get("stackingCount")
        if capture.get("hasStacking") and count:
            exp = (capture.get("cameraParams") or {}).get("exposureMicroSec") or 0
            dur = _duration(count * exp / 1_000_000)
            return f"{target}: {count} stacked" + (f" ({dur})" if dur else "")
        step_type, progress = _current_step(op.get("steps"))
        label = OBS_STEP_LABELS.get(step_type or "", "Observing")
        return f"{target}: {label}{_pct(progress) if step_type == 'AUTO_FOCUS' else ''}"

    if op_type == "AUTO_INIT":
        return f"Initialization: {autoinit_step_label(raw) or 'Starting'}"

    if op_type == "PLAN":
        name = op.get("planName") or "Plan"
        state = PLAN_STATE_LABELS.get(op.get("state") or "", op.get("state") or "running")
        targets = [t for t in (op.get("targets") or []) if isinstance(t, dict)]
        for i, tgt in enumerate(targets):
            if tgt.get("storeState") == "OBSERVING":
                obj = (tgt.get("target") or {}).get("objectName") or "?"
                return f"{name} — {state} ({obj}, {i + 1}/{len(targets)})"
        return f"{name} — {state}"

    return OPERATION_TYPE_LABELS.get(op_type or "", "Busy")
