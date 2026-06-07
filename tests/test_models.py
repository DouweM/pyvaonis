"""Model parsing/serialisation tests."""

from __future__ import annotations

import pytest

from pyvaonis.models import ObservationBody
from pyvaonis.models import VaonisStatus


def test_observation_payload_omits_mosaic_and_store_by_default() -> None:
    body = ObservationBody(object_id="M42", do_stacking=True)
    payload = body.to_payload()
    assert "mosaic" not in payload
    assert "store" not in payload


def test_observation_payload_mosaic_and_multi_night() -> None:
    body = ObservationBody(
        object_id="M42", do_stacking=True, mosaic_width=3.2, mosaic_height=2.2, resumable=True
    )
    payload = body.to_payload()
    assert payload["mosaic"] == {"widthDegree": 3.2, "heightDegree": 2.2}
    assert payload["store"] == {"state": "TO_BE_RESUMABLE"}
    # the helper fields themselves are never serialised raw
    assert "mosaic_width" not in payload and "resumable" not in payload


def test_status_parses_aliases_and_keeps_extra() -> None:
    status = VaonisStatus.model_validate(
        {
            "challenge": "xABC",
            "telescopeId": "STELLINA-1",
            "bootCount": 7,
            "masterDeviceId": "dev-1",
            "internalBattery": {"percent": 80},  # unmodelled -> kept via extra
        }
    )
    assert status.telescope_id == "STELLINA-1"
    assert status.boot_count == 7
    assert status.can_authenticate
    assert status.auth_args() == ("xABC", "STELLINA-1", 7)
    assert status.raw["internalBattery"] == {"percent": 80}


def test_status_without_challenge_cannot_authenticate() -> None:
    status = VaonisStatus.model_validate({"telescopeId": "x", "bootCount": 1})
    assert not status.can_authenticate
    with pytest.raises(ValueError):
        status.auth_args()


def test_observation_body_drops_none_and_aliases() -> None:
    payload = ObservationBody(object_name="M42", ra=83.82, de=-5.39).to_payload()
    assert payload["objectName"] == "M42"
    assert payload["doStacking"] is False  # no stacking params supplied → single frames
    assert payload["algorithm"] == "AUTO"
    assert payload["ra"] == 83.82
    assert "gain" not in payload  # None dropped
    assert "exposureMicroSec" not in payload
    assert "brightZoneOffset" not in payload  # omitted for AUTO
    assert "histogramLow" not in payload  # omitted when not stacking


def test_raw_is_cached() -> None:
    # raw is read many times per status update; it must be computed once, not re-dumped each access.
    status = VaonisStatus.model_validate({"telescopeId": "stellina-x", "currentOperation": None})
    assert status.raw is status.raw  # same object -> cached
    assert status.raw["telescopeId"] == "stellina-x"
