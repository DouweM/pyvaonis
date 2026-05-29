"""Typed views over the telescope's status and request bodies.

The status JSON (pushed over socket.io and parsed by the app into ``StellinaStatus``)
is large and firmware-dependent, so :class:`StellinaStatus` keeps the full payload in
``raw`` and only types the fields we rely on. ``extra="allow"`` keeps unknown fields.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class StellinaStatus(BaseModel):
    """Subset of ``com.vaonis.instruments.sdk.models.status.StellinaStatus``."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    challenge: str | None = None
    telescope_id: str | None = Field(default=None, alias="telescopeId")
    boot_count: int | None = Field(default=None, alias="bootCount")
    master_device_id: str | None = Field(default=None, alias="masterDeviceId")
    initialized: bool | None = None
    model: str | None = None

    @property
    def raw(self) -> dict[str, Any]:
        """The full status payload, including fields not modelled above."""
        return self.model_dump(by_alias=True)

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
    observatory_name: str = Field(default="pystellina", serialization_alias="observatoryName")
    skip_auto_focus: bool = Field(default=False, serialization_alias="skipAutoFocus")


class ObservationBody(BaseModel):
    """Subset of ``general/startObservation`` body (StartObservationBody.kt).

    Unset (None) fields are omitted so the firmware applies its defaults.
    """

    model_config = ConfigDict(populate_by_name=True)

    object_id: str = Field(default="", serialization_alias="objectId")
    object_name: str = Field(default="", serialization_alias="objectName")
    object_type: str = Field(default="", serialization_alias="objectType")
    ra: float | None = None
    de: float | None = None
    rot: float | None = None
    gain: int | None = None
    exposure_micro_sec: int | None = Field(default=None, serialization_alias="exposureMicroSec")
    do_stacking: bool = Field(default=True, serialization_alias="doStacking")

    def to_payload(self) -> dict[str, Any]:
        """JSON body with ``None`` fields dropped."""
        return self.model_dump(by_alias=True, exclude_none=True)
