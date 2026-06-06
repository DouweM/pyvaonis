"""Live observation progress + image access.

During an observation the telescope **live-stacks**: ``status.currentOperation`` (when its
``type`` is ``OBSERVATION``) holds a ``capture`` whose ``images`` list grows over time, so
integration = ``stackingCount x cameraParams.exposureMicroSec`` — the app's "layering over a
longer exposure". The latest stacked frame is ``capture.images[-1]``.

Shapes confirmed against firmware 2.35.7 (see PROTOCOL.md). Image URLs are served at
``http://<ip>:8082<path>`` (e.g. ``/files/captures/<storeId>/images/IMG_NNNN.jpg``), no auth.
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
        """Full HTTP URL with the per-frame selector query (no auth).

        The ``androidImageIndex``/``androidCaptureId`` query makes the firmware *render* this exact
        stacked frame on demand — correct, but slow while it is also stacking. For the already-
        written file use :meth:`static_url` / :attr:`ftp_path` instead.
        """
        return (
            f"http://{ip}:{HTTP_PORT}{self.url_path}"
            f"?androidImageIndex={self.index}&androidCaptureId={self.capture_id}"
        )

    def static_url(self, ip: str) -> str:
        """HTTP URL of the already-written frame file, with no render-triggering query string."""
        return f"http://{ip}:{HTTP_PORT}{self.url_path}"

    @property
    def ftp_path(self) -> str:
        """FTP path of the same frame file (the ``/files`` HTTP root maps to ``/system`` on FTP)."""
        path = self.url_path
        if path.startswith("/files/"):
            path = "/system/" + path[len("/files/") :]
        # HTTP advertises plan frames under /files/plans/ but FTP stores them under /system/plan/.
        if path.startswith("/system/plans/"):
            path = "/system/plan/" + path[len("/system/plans/") :]
        return path


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
    exposure_us = (capture.get("cameraParams") or {}).get("exposureMicroSec")
    if count is None or exposure_us is None:
        return None
    return count * exposure_us / 1_000_000.0


def _observation_op(status_raw: dict[str, Any]) -> dict[str, Any] | None:
    """The active observation operation, or None when not observing."""
    op = status_raw.get("currentOperation")
    if isinstance(op, dict) and op.get("type") == "OBSERVATION" and not op.get("stopped"):
        return op
    return None


class ObservationProgress(BaseModel):
    """A compact view of the current observation, derived from status."""

    object_id: str | None = None
    object_name: str | None = None
    observation_type: str | None = None
    current_step: str | None = None
    stacking_count: int | None = None
    total_stacking_count: int | None = None
    integration_seconds: float | None = None
    stopped: bool = False
    live_image: LiveImage | None = None

    @classmethod
    def from_status(cls, status_raw: dict[str, Any]) -> ObservationProgress | None:
        """Parse the current observation from a raw status dict, or None if not observing."""
        op = _observation_op(status_raw)
        if op is None:
            return None
        target = op.get("target") or {}
        capture = op.get("capture") or {}
        store = op.get("store") or {}
        steps = [s for s in (op.get("steps") or []) if isinstance(s, dict)]
        current_step = (steps[-1].get("name") or steps[-1].get("type")) if steps else None
        return cls(
            object_id=target.get("objectId"),
            object_name=target.get("objectName") or target.get("objectId"),
            observation_type=op.get("observationType"),
            current_step=current_step,
            stacking_count=capture.get("stackingCount"),
            total_stacking_count=store.get("totalStackingCount"),
            integration_seconds=_integration_seconds(capture),
            stopped=bool(op.get("stopped")),
            live_image=_capture_image(capture) if capture else None,
        )


def _walk_last_images(node: Any, out: list[LiveImage]) -> None:
    """Collect ``lastImage`` entries from finished operations (plans, etc.), recursively."""
    if isinstance(node, dict):
        last = node.get("lastImage")
        if isinstance(last, dict) and last.get("url"):
            out.append(
                LiveImage(
                    capture_id="",
                    index=int(last.get("index", 0)),
                    url_path=last["url"],
                    stacking_count=last.get("stackingCount"),
                )
            )
        for value in node.values():
            _walk_last_images(value, out)
    elif isinstance(node, list):
        for item in node:
            _walk_last_images(item, out)


def recent_images(status_raw: dict[str, Any]) -> list[LiveImage]:
    """Latest frame of the current capture plus finished operations' last images."""
    images: list[LiveImage] = []
    op = _observation_op(status_raw)
    if op is not None and isinstance(op.get("capture"), dict):
        current = _capture_image(op["capture"])
        if current is not None:
            images.append(current)
    _walk_last_images(status_raw.get("previousOperations"), images)
    return images
