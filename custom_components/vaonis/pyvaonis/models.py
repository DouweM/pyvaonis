"""Typed views over the telescope's status and request bodies.

The status JSON (pushed over socket.io and parsed by the app into ``VaonisStatus``)
is large and firmware-dependent, so :class:`VaonisStatus` keeps the full payload in
``raw`` and only types the fields we rely on. ``extra="allow"`` keeps unknown fields.
"""

from __future__ import annotations

from functools import cached_property
from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

# In the real status JSON the only top-level operation key is `currentOperation` (single,
# polymorphic by `type`); the per-mode names are derived in-app, never present as JSON keys.
# A non-stopped `currentOperation` means the scope is busy (matches the app's canStartOperation).
_OPERATION_KEYS = ("currentOperation",)


class VaonisStatus(BaseModel):
    """Subset of ``com.vaonis.instruments.sdk.models.status.StellinaStatus``."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    challenge: str | None = None
    telescope_id: str | None = Field(default=None, alias="telescopeId")
    boot_count: int | None = Field(default=None, alias="bootCount")
    master_device_id: str | None = Field(default=None, alias="masterDeviceId")
    initialized: bool | None = None
    shutting_down: bool | None = Field(default=None, alias="shuttingDown")
    model: str | None = None

    @cached_property
    def raw(self) -> dict[str, Any]:
        """The full status payload, including fields not modelled above.

        Cached: a status object is created once per push and read by many entities (each digging a
        few values), so re-running ``model_dump`` on every access was dozens of full serializations
        of this large nested dict per update — enough synchronous work on the event loop to make the
        socket read loop fall behind and deliver updates in bursts. Treat the result as read-only."""
        return self.model_dump(by_alias=True)

    @property
    def active_operation(self) -> str | None:
        """Name of the running operation (a non-stopped ``current*Operation``), or None if idle."""
        raw = self.raw
        for key in _OPERATION_KEYS:
            op = raw.get(key)
            if isinstance(op, dict) and not op.get("stopped"):
                return key
        return None

    @property
    def is_busy(self) -> bool:
        """Whether an operation is currently running."""
        return self.active_operation is not None

    @property
    def position(self) -> tuple[float, float] | None:
        """The scope's own (latitude, longitude) from ``position``/observatory, or None."""
        pos = self.raw.get("position")
        if isinstance(pos, dict):
            lat, lon = pos.get("latitude"), pos.get("longitude")
            if isinstance(lat, int | float) and isinstance(lon, int | float):
                return float(lat), float(lon)
        return None

    @property
    def can_authenticate(self) -> bool:
        """Whether enough fields are present to build an auth header."""
        return (
            self.challenge is not None
            and self.telescope_id is not None
            and self.boot_count is not None
        )

    def auth_args(self) -> tuple[str, str, int]:
        """``(challenge, telescope_id, boot_count)`` for :func:`auth.build_auth_header`."""
        if not self.can_authenticate:
            raise ValueError("status is missing challenge/telescopeId/bootCount")
        assert self.challenge is not None and self.telescope_id is not None
        assert self.boot_count is not None
        return self.challenge, self.telescope_id, self.boot_count


class AutoInitBody(BaseModel):
    """Body for ``general/startAutoInit`` (AutoInitBody.kt)."""

    latitude: float
    longitude: float
    time: int  # epoch millis
    observatory_id: str = Field(default="", serialization_alias="observatoryId")
    observatory_name: str = Field(default="pyvaonis", serialization_alias="observatoryName")
    skip_auto_focus: bool = Field(default=False, serialization_alias="skipAutoFocus")


class ObservationBody(BaseModel):
    """Subset of ``general/startObservation`` body (StartObservationBody.kt).

    Unset (None) fields are omitted so the firmware applies its defaults.
    """

    model_config = ConfigDict(populate_by_name=True)

    object_id: str = Field(default="", serialization_alias="objectId")
    object_name: str = Field(default="", serialization_alias="objectName")
    object_type: str = Field(default="", serialization_alias="objectType")
    target_type: str | None = Field(
        default=None, serialization_alias="targetType"
    )  # CATALOG|MANUAL
    ra: float | None = None
    de: float | None = None
    rot: float | None = None
    gain: int | None = None
    exposure_micro_sec: int | None = Field(default=None, serialization_alias="exposureMicroSec")
    # The firmware requires the six histogram/background params *iff* doStacking is true (and rejects
    # the call if any is missing); for non-stacking targets (planets/Moon/stars) they must be omitted.
    do_stacking: bool = Field(default=False, serialization_alias="doStacking")
    histogram_enabled: bool | None = Field(default=None, serialization_alias="histogramEnabled")
    histogram_low: float | None = Field(default=None, serialization_alias="histogramLow")
    histogram_medium: float | None = Field(default=None, serialization_alias="histogramMedium")
    histogram_high: float | None = Field(default=None, serialization_alias="histogramHigh")
    background_enabled: bool | None = Field(default=None, serialization_alias="backgroundEnabled")
    background_polyorder: float | None = Field(
        default=None, serialization_alias="backgroundPolyorder"
    )
    # The app sends STANDARD + AUTO/AUTO for catalog targets; brightZoneOffset is only included for
    # DEEP_SKY* algorithms, so it stays None (omitted) for AUTO.
    observation_type: str = Field(default="STANDARD", serialization_alias="observationType")
    algorithm: str = Field(default="AUTO", serialization_alias="algorithm")
    bright_zone_offset: str | None = Field(default=None, serialization_alias="brightZoneOffset")
    # Mosaic ("Advanced observation"): capture a field bigger than one frame. Only the angular size in
    # degrees is sent as a nested ``mosaic`` object; the firmware computes the tiling. Deep-sky only.
    mosaic_width: float | None = None
    mosaic_height: float | None = None
    # Multi-night: persist the capture so it can be resumed on later nights (the app sets this in the
    # start body as ``store.state = TO_BE_RESUMABLE``; equivalent to capture/setToBeResumable later).
    resumable: bool = False

    def to_payload(self) -> dict[str, Any]:
        """JSON body with ``None`` fields dropped; mosaic/store nested as the firmware expects."""
        body = self.model_dump(
            by_alias=True,
            exclude_none=True,
            exclude={"mosaic_width", "mosaic_height", "resumable"},
        )
        if self.mosaic_width is not None and self.mosaic_height is not None:
            body["mosaic"] = {"widthDegree": self.mosaic_width, "heightDegree": self.mosaic_height}
        if self.resumable:
            body["store"] = {"state": "TO_BE_RESUMABLE"}
        return body


class PlanTargetBody(BaseModel):
    """One scheduled target of a native plan (``PlanMyNightTargetBody``).

    The firmware runs each target in its own ``[startTime, endTime]`` window (epoch ms), auto-
    advancing when ``endTime`` passes. ``params`` is a full :class:`ObservationBody`; ``storeId``
    resumes a stored (multi-night) capture instead of starting fresh.
    """

    model_config = ConfigDict(populate_by_name=True)

    start_time: int = Field(serialization_alias="startTime")  # epoch ms
    end_time: int = Field(serialization_alias="endTime")  # epoch ms
    store_id: str | None = Field(default=None, serialization_alias="storeId")
    params: ObservationBody | None = None

    def to_payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"startTime": self.start_time, "endTime": self.end_time}
        if self.store_id is not None:
            body["storeId"] = self.store_id
        if self.params is not None:
            body["params"] = self.params.to_payload()
        return body


class PlanBody(BaseModel):
    """Body for ``planner/startPlan`` (``PlanMyNightBody``) — the native "Plan My Night".

    The firmware executes the whole plan autonomously (auto-init included), so it survives the
    controller disconnecting — unlike client-driven looping.
    """

    model_config = ConfigDict(populate_by_name=True)

    plan_id: str = Field(serialization_alias="planId")
    plan_version: str = Field(serialization_alias="planVersion")
    plan_name: str = Field(serialization_alias="planName")
    targets: list[PlanTargetBody]
    latitude: float
    longitude: float
    observatory_id: str = Field(default="", serialization_alias="observatoryId")
    observatory_name: str = Field(default="pyvaonis", serialization_alias="observatoryName")
    user_id: int = Field(default=0, serialization_alias="userId")
    device_id: str = Field(serialization_alias="deviceId")
    app_version: str = Field(serialization_alias="appVersion")

    def to_payload(self) -> dict[str, Any]:
        return {
            "planId": self.plan_id,
            "planVersion": self.plan_version,
            "planName": self.plan_name,
            "targets": [t.to_payload() for t in self.targets],
            "latitude": self.latitude,
            "longitude": self.longitude,
            "observatoryId": self.observatory_id,
            "observatoryName": self.observatory_name,
            "userId": self.user_id,
            "deviceId": self.device_id,
            "appVersion": self.app_version,
        }
