"""Full-res export URL building and download, against stubbed transport methods."""

from __future__ import annotations

from typing import Any

import pytest

from pystellina import StellinaClient
from pystellina.client import StellinaCommandError


def _client() -> StellinaClient:
    return StellinaClient(ip="10.0.0.1")


async def test_export_tiff_posts_and_builds_url() -> None:
    client = _client()
    seen: dict[str, Any] = {}

    async def fake_request(method: str, endpoint: str, body: Any = None) -> dict[str, Any]:
        seen.update(method=method, endpoint=endpoint, body=body)
        return {"result": {"url": "/exports/abc.tif"}}

    client.request = fake_request  # type: ignore[method-assign]
    url = await client.export_url("abc", "tiff")
    assert seen == {
        "method": "POST",
        "endpoint": "capture/exportImageTiff",
        "body": {"captureId": "abc"},
    }
    assert url == "http://10.0.0.1:8082/exports/abc.tif"


async def test_export_jxl_posts_with_query_no_body() -> None:
    client = _client()
    seen: dict[str, Any] = {}

    async def fake_request(method: str, endpoint: str, body: Any = None) -> dict[str, Any]:
        seen.update(method=method, endpoint=endpoint, body=body)
        return {"result": {"url": "/exports/abc.jxl"}}

    client.request = fake_request  # type: ignore[method-assign]
    assert await client.export_url("abc", "jxl") == "http://10.0.0.1:8082/exports/abc.jxl"
    assert seen == {
        "method": "POST",
        "endpoint": "capture/exportImageJpegXl?captureId=abc",
        "body": None,
    }


async def test_export_capture_downloads_bytes() -> None:
    client = _client()

    async def fake_request(method: str, endpoint: str, body: Any = None) -> dict[str, Any]:
        return {"result": {"url": "/exports/abc.tif"}}

    async def fake_fetch(image: Any) -> bytes:
        assert image == "http://10.0.0.1:8082/exports/abc.tif"
        return b"TIFFBYTES"

    client.request = fake_request  # type: ignore[method-assign]
    client.fetch_image = fake_fetch  # type: ignore[method-assign]
    assert await client.export_capture("abc", "tiff") == b"TIFFBYTES"


async def test_unknown_format_raises() -> None:
    with pytest.raises(StellinaCommandError):
        await _client().export_url("abc", "png")
