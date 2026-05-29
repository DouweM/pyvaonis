"""Live observation progress + image access.

During an observation the telescope **live-stacks**: a single capture accumulates frames
(``stackingCount``), so its integration time grows as ``stackingCount x exposure`` — this is the
app's "layering over a longer exposure". The latest stacked frame is fetchable as an image;
finished captures appear under ``previousCaptures``.

This module parses the relevant slice of the (large, firmware-dependent) status JSON into small
typed views, and builds the image URLs (served at ``http://<ip>:8082<path>``, no auth).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .const import HTTP_PORT


class LiveImage(BaseModel):
    """A single stacked frame available for download."""

    capture_id: str
    index: int
    url_path: str
    stacking_count: int | None = None

    def url(self, ip: str) -> str:
        """Full HTTP URL (no auth). The query is cache-busting per stacked frame."""
        return (
            f"http://{ip}:{HTTP_PORT}{self.url_path}"
            f"?androidImageIndex={self.index}&androidCaptureId={self.capture_id}"
        )


def _capture_image(capture: dict[str, Any]) -> LiveImage | None:
    images = capture.get("images") or []
    if not images:
        return None
    img = images[-1]  # latest stacked frame
    url_path = img.get("url")
    if not url_path:
        return None
    return LiveImage(
        capture_id=capture.get("id", ""),
        index=int(img.get("index", 0)),
        url_path=url_path,
        stacking_count=img.get("stackingCount") or capture.get("stackingCount"),
    )


def _integration_seconds(capture: dict[str, Any]) -> float | None:
    count = capture.get("stackingCount")
    exposure_us = capture.get("userExposureMicroSec")
    if count is None or exposure_us is None:
        return None
    return count * exposure_us / 1_000_000.0


class ObservationProgress(BaseModel):
    """A compact view of the current observation, derived from status."""

    object_id: str | None = None
    object_name: str | None = None
    observation_type: str | None = None
    current_step: str | None = None
    step_progress: float | None = None
    stacking_count: int | None = None
    total_stacking_count: int | None = None
    integration_seconds: float | None = None
    stopped: bool = False
    live_image: LiveImage | None = None

    @classmethod
    def from_status(cls, status_raw: dict[str, Any]) -> ObservationProgress | None:
        """Parse the current observation from a raw status dict, or None if idle."""
        op = status_raw.get("currentObservationOperation")
        if not isinstance(op, dict):
            return None
        target = op.get("target") or {}
        capture = op.get("capture") or {}
        steps = [s for s in (op.get("steps") or []) if isinstance(s, dict)]
        active = next(
            (s for s in steps if (s.get("progress") or 0) < 1), steps[-1] if steps else None
        )
        return cls(
            object_id=target.get("objectId"),
            object_name=target.get("objectName") or target.get("objectId"),
            observation_type=op.get("observationType"),
            current_step=(active or {}).get("name"),
            step_progress=(active or {}).get("progress"),
            stacking_count=capture.get("stackingCount"),
            total_stacking_count=capture.get("totalStackingCount"),
            integration_seconds=_integration_seconds(capture),
            stopped=bool(op.get("stopped")),
            live_image=_capture_image(capture) if capture else None,
        )


def recent_images(status_raw: dict[str, Any]) -> list[LiveImage]:
    """Latest frame of the current capture plus each previous capture, newest first."""
    op = status_raw.get("currentObservationOperation")
    if not isinstance(op, dict):
        return []
    captures: list[dict[str, Any]] = []
    if isinstance(op.get("capture"), dict):
        captures.append(op["capture"])
    captures.extend(c for c in (op.get("previousCaptures") or []) if isinstance(c, dict))
    images = [_capture_image(c) for c in captures]
    return [img for img in images if img is not None]
