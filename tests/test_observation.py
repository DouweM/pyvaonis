"""Observation progress parsing and live-image URL building (firmware-2.35.7 shapes)."""

from __future__ import annotations

from pystellina.observation import ObservationProgress
from pystellina.observation import recent_images

RAW = {
    "currentOperation": {
        "type": "OBSERVATION",
        "stopped": False,
        "observationType": "STANDARD",
        "target": {"type": "CATALOG", "objectId": "M104", "objectName": "Sombrero Galaxy"},
        "steps": [
            {"type": "POINT_DEEP_SKY", "name": "initial", "steps": [{"type": "GO_TARGET"}]},
            {"type": "CAPTURE", "captureType": "INITIAL"},
        ],
        "capture": {
            "id": "capture-105-295-f8bd80",
            "cameraParams": {"exposureMicroSec": 10_000_000, "gain": 200},
            "stackingCount": 42,
            "images": [
                {
                    "index": 40,
                    "url": "/files/captures/M104/images/IMG_0041.jpg",
                    "stackingCount": 41,
                },
                {
                    "index": 41,
                    "url": "/files/captures/M104/images/IMG_0042.jpg",
                    "stackingCount": 42,
                },
            ],
        },
        "store": {"state": "NON_RESUMABLE", "storeId": "M104", "totalStackingCount": 42},
    },
    "previousOperations": {
        "plan": {
            "targets": [
                {
                    "attempts": [
                        {
                            "lastImage": {
                                "index": 235,
                                "url": "/files/plans/t1/IMG_0236.jpg",
                                "stackingCount": 236,
                            }
                        }
                    ]
                }
            ]
        }
    },
}


def test_progress_parses_target_step_and_stacking() -> None:
    obs = ObservationProgress.from_status(RAW)
    assert obs is not None
    assert obs.object_name == "Sombrero Galaxy"
    assert obs.current_step == "CAPTURE"  # last step's type
    assert obs.stacking_count == 42
    assert obs.total_stacking_count == 42
    assert obs.integration_seconds == 420.0  # 42 frames x 10s


def test_live_image_url() -> None:
    obs = ObservationProgress.from_status(RAW)
    assert obs is not None and obs.live_image is not None
    assert obs.live_image.index == 41  # latest stacked frame
    assert obs.live_image.url("10.0.0.1") == (
        "http://10.0.0.1:8082/files/captures/M104/images/IMG_0042.jpg"
        "?androidImageIndex=41&androidCaptureId=capture-105-295-f8bd80"
    )


def test_recent_images_includes_current_and_previous() -> None:
    images = recent_images(RAW)
    paths = [i.url_path for i in images]
    assert "/files/captures/M104/images/IMG_0042.jpg" in paths  # current capture
    assert "/files/plans/t1/IMG_0236.jpg" in paths  # finished plan


def test_not_observing_returns_none() -> None:
    assert ObservationProgress.from_status({}) is None
    assert ObservationProgress.from_status({"currentOperation": {"type": "PARK"}}) is None
    assert (
        ObservationProgress.from_status(
            {"currentOperation": {"type": "OBSERVATION", "stopped": True}}
        )
        is None
    )
    assert recent_images({}) == []
