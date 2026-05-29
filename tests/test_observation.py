"""Observation progress parsing and live-image URL building."""

from __future__ import annotations

from pystellina.observation import ObservationProgress
from pystellina.observation import recent_images

RAW = {
    "currentObservationOperation": {
        "stopped": False,
        "observationType": "DEEP_SKY",
        "target": {"objectId": "M42", "objectName": "Orion Nebula"},
        "steps": [
            {"name": "pointing", "progress": 1.0},
            {"name": "capture", "progress": 0.5},
        ],
        "capture": {
            "id": "cap1",
            "stackingCount": 12,
            "totalStackingCount": 60,
            "userExposureMicroSec": 10_000_000,
            "images": [
                {"index": 0, "url": "/captures/cap1/0.jpg", "stackingCount": 11},
                {"index": 1, "url": "/captures/cap1/1.jpg", "stackingCount": 12},
            ],
        },
        "previousCaptures": [
            {"id": "cap0", "images": [{"index": 5, "url": "/captures/cap0/5.jpg"}]}
        ],
    }
}


def test_progress_parses_target_step_and_stacking() -> None:
    obs = ObservationProgress.from_status(RAW)
    assert obs is not None
    assert obs.object_name == "Orion Nebula"
    assert obs.current_step == "capture"  # first step still in progress
    assert obs.step_progress == 0.5
    assert obs.stacking_count == 12
    assert obs.total_stacking_count == 60
    assert obs.integration_seconds == 120.0  # 12 frames x 10s


def test_live_image_url() -> None:
    obs = ObservationProgress.from_status(RAW)
    assert obs is not None and obs.live_image is not None
    assert obs.live_image.index == 1  # latest stacked frame
    assert obs.live_image.url("10.0.0.1") == (
        "http://10.0.0.1:8082/captures/cap1/1.jpg?androidImageIndex=1&androidCaptureId=cap1"
    )


def test_recent_images_includes_current_and_previous() -> None:
    images = recent_images(RAW)
    assert [i.capture_id for i in images] == ["cap1", "cap0"]


def test_idle_status_has_no_observation() -> None:
    assert ObservationProgress.from_status({}) is None
    assert recent_images({}) == []
