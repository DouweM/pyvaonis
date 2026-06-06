"""Browse and download the telescope's saved image library over anonymous FTP.

The telescope runs an anonymous FTP server (``10.0.0.1:21``); finished observations live
under ``/user/<observation>/...`` as ``.jpg`` / ``.tif`` files. ``ftplib`` is synchronous, so
the public helpers run it in a thread to stay async-friendly (Home Assistant, asyncio CLIs).
"""

from __future__ import annotations

import asyncio
import posixpath
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from ftplib import FTP
from ftplib import all_errors
from ftplib import error_perm

from .const import DEFAULT_IP
from .const import FTP_PORT
from .const import FTP_ROOT


@dataclass
class FtpEntry:
    """A file or directory in the saved library."""

    name: str
    path: str
    is_dir: bool
    size: int | None = None
    modified: datetime | None = None  # UTC, from the MLSD ``modify`` fact (when available)


def _parse_mlsd_time(value: str | None) -> datetime | None:
    """Parse an MLSD ``modify`` fact (``YYYYMMDDHHMMSS[.frac]``, UTC) to a datetime."""
    if not value or len(value) < 14 or not value[:14].isdigit():
        return None
    return datetime.strptime(value[:14], "%Y%m%d%H%M%S").replace(tzinfo=UTC)


class _NatFTP(FTP):
    """FTP that ignores the server-advertised passive IP (like ``curl --ftp-skip-pasv-ip``).

    The Stellina advertises its own ``10.0.0.1`` in PASV replies; across a Wi-Fi bridge/NAT
    that address is unreachable, so we always make the data connection to the control host
    (which is also correct when connected directly). The data *port* still has to be reachable
    — through a bridge that needs the router's FTP NAT/conntrack helper.
    """

    def makepasv(self) -> tuple[str, int]:
        _, port = super().makepasv()
        return self.host, port


def _connect(ip: str, timeout: float) -> FTP:
    ftp = _NatFTP()
    ftp.connect(ip, FTP_PORT, timeout=timeout)
    ftp.login()  # anonymous
    return ftp


def _list(ip: str, path: str, timeout: float) -> list[FtpEntry]:
    ftp = _connect(ip, timeout)
    try:
        entries: list[FtpEntry] = []
        try:
            for name, facts in ftp.mlsd(path):
                if name in (".", ".."):
                    continue
                size = facts.get("size")
                entries.append(
                    FtpEntry(
                        name=name,
                        path=posixpath.join(path, name),
                        is_dir=facts.get("type") == "dir",
                        size=int(size) if size and size.isdigit() else None,
                        modified=_parse_mlsd_time(facts.get("modify")),
                    )
                )
        except error_perm:
            # Server without MLSD: fall back to NLST and probe directories with CWD.
            ftp.cwd(path)
            for name in ftp.nlst():
                base = posixpath.basename(name)
                if base in (".", ".."):
                    continue
                full = posixpath.join(path, base)
                is_dir = True
                try:
                    ftp.cwd(full)
                    ftp.cwd(path)
                except error_perm:
                    is_dir = False
                entries.append(FtpEntry(name=base, path=full, is_dir=is_dir))
        entries.sort(key=lambda e: (not e.is_dir, e.name))
        return entries
    finally:
        ftp.close()


def _download(ip: str, path: str, timeout: float) -> bytes:
    ftp = _connect(ip, timeout)
    buf = bytearray()
    try:
        ftp.retrbinary(f"RETR {path}", buf.extend)
        return bytes(buf)
    finally:
        ftp.close()


def _modified(ip: str, path: str, timeout: float) -> datetime | None:
    ftp = _connect(ip, timeout)
    try:
        # MDTM <path> -> "213 YYYYMMDDHHMMSS[.frac]" (UTC); widely supported even without MLSD.
        _, _, stamp = ftp.sendcmd(f"MDTM {path}").partition(" ")
        return _parse_mlsd_time(stamp.strip())
    except all_errors:
        return None
    finally:
        ftp.close()


async def list_dir(
    path: str = FTP_ROOT, *, ip: str = DEFAULT_IP, timeout: float = 20.0
) -> list[FtpEntry]:
    """List a directory in the saved library (defaults to ``/system/captures``)."""
    from .client import VaonisConnectionError  # local import: avoid client<->ftp import cycle

    try:
        return await asyncio.to_thread(_list, ip, path, timeout)
    except all_errors as err:  # ftplib's Error/OSError/EOFError (incl. 550, refused, timeout)
        raise VaonisConnectionError(f"FTP list {path!r} failed: {err}") from err


async def download(path: str, *, ip: str = DEFAULT_IP, timeout: float = 60.0) -> bytes:
    """Download a saved file by its FTP path."""
    from .client import VaonisConnectionError

    try:
        return await asyncio.to_thread(_download, ip, path, timeout)
    except all_errors as err:
        raise VaonisConnectionError(f"FTP download {path!r} failed: {err}") from err


async def modified_time(
    path: str, *, ip: str = DEFAULT_IP, timeout: float = 20.0
) -> datetime | None:
    """The file's modification time (UTC) via MDTM, or None if the server won't report it."""
    return await asyncio.to_thread(_modified, ip, path, timeout)
