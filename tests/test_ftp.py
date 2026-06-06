"""FTP library browsing/download against a fake ftplib.FTP."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from typing import ClassVar

import pytest

import pyvaonis.ftp as ftp_mod


class FakeFTP:
    _dirs: ClassVar[dict] = {
        "/user": [
            ("obs1", {"type": "dir"}),
            ("note.txt", {"type": "file", "size": "10", "modify": "20260530020915"}),
        ],
        "/user/obs1": [("M42.jpg", {"type": "file", "size": "123"})],
    }
    _blobs: ClassVar[dict] = {"/user/obs1/M42.jpg": b"JPEGDATA"}

    def connect(self, *args: object, **kwargs: object) -> None: ...
    def login(self, *args: object, **kwargs: object) -> None: ...
    def close(self) -> None: ...

    def mlsd(self, path: str = ""):
        return iter(self._dirs.get(path, []))

    def retrbinary(self, cmd: str, callback) -> None:
        callback(self._blobs[cmd.split(" ", 1)[1]])


@pytest.fixture(autouse=True)
def _fake_ftp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ftp_mod, "_NatFTP", FakeFTP)


async def test_list_dir_sorts_dirs_first() -> None:
    entries = await ftp_mod.list_dir("/user", ip="10.0.0.1")
    assert [(e.name, e.is_dir) for e in entries] == [("obs1", True), ("note.txt", False)]
    assert entries[1].size == 10
    assert entries[0].path == "/user/obs1"
    # MLSD modify fact is parsed to a UTC datetime (so images carry their real capture time)
    assert entries[1].modified == datetime(2026, 5, 30, 2, 9, 15, tzinfo=UTC)
    assert entries[0].modified is None


def test_parse_mlsd_time() -> None:
    assert ftp_mod._parse_mlsd_time("20260530020915") == datetime(2026, 5, 30, 2, 9, 15, tzinfo=UTC)
    assert ftp_mod._parse_mlsd_time("20260530020915.250") == datetime(
        2026, 5, 30, 2, 9, 15, tzinfo=UTC
    )
    assert ftp_mod._parse_mlsd_time(None) is None
    assert ftp_mod._parse_mlsd_time("bogus") is None


async def test_download_returns_bytes() -> None:
    data = await ftp_mod.download("/user/obs1/M42.jpg", ip="10.0.0.1")
    assert data == b"JPEGDATA"
