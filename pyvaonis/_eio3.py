"""Minimal async Engine.IO v3 / Socket.IO v2 client over an aiohttp websocket.

The Stellina firmware runs a Socket.IO 2.x server (Engine.IO protocol **3**, ``EIO=3`` — confirmed
from the decompiled app). ``python-socketio`` 5.x / ``python-engineio`` 4.x hardcode ``EIO=4`` and
cannot talk to it (you connect but never receive events). This implements exactly what we need:

- websocket-transport connect with ``EIO=3``,
- receive Socket.IO events (e.g. ``STATUS_UPDATED``, ``CONTROL_ERROR``),
- emit events (``message``/``takeControl``/...),
- EIO3 **client-initiated** heartbeat (send ``2`` ping every ``pingInterval``; also reply ``3`` to
  any server ping, so we're correct regardless of heartbeat direction).

Frame grammar (text):
  ``0<json>`` engineio OPEN · ``2`` ping · ``3`` pong · ``4<sio>`` message
  Socket.IO packet: ``0`` connect · ``1`` disconnect · ``2[<ack>]<json-array>`` event · ``4`` error
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

Handler = Callable[..., Awaitable[None] | None]


def encode_event(event: str, args: list[Any]) -> str:
    """Encode a Socket.IO event as an EIO3 message frame: ``42["event", *args]``."""
    return "42" + json.dumps([event, *args], separators=(",", ":"))


def decode_frame(frame: str) -> tuple[str, Any]:
    """Decode a text frame into ``(kind, payload)``.

    kinds: open(dict) · ping · pong · eio_close · connect · disconnect · event((name,args)) ·
    error(str) · other(raw).
    """
    if not frame:
        return ("other", frame)
    etype, body = frame[0], frame[1:]
    if etype == "0":
        try:
            return ("open", json.loads(body) if body else {})
        except ValueError:
            return ("other", frame)
    if etype == "1":
        return ("eio_close", None)
    if etype == "2":
        return ("ping", None)
    if etype == "3":
        return ("pong", None)
    if etype == "4":  # engineio message -> socket.io packet
        if not body:
            return ("other", frame)
        stype, rest = body[0], body[1:]
        if stype == "0":
            return ("connect", None)
        if stype == "1":
            return ("disconnect", None)
        if stype == "2":  # event: optional namespace + ack id, then a JSON array
            start = rest.find("[")
            if start == -1:
                return ("other", frame)
            try:
                arr = json.loads(rest[start:])
            except ValueError:
                return ("other", frame)
            name = arr[0] if arr else ""
            return ("event", (name, arr[1:]))
        if stype == "4":
            return ("error", rest)
    return ("other", frame)


class EngineIO3Client:
    """Just-enough Socket.IO v2 client for the telescope's status/control channel."""

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        self._session = session
        self._owns_session = session is None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._handlers: dict[str, list[Handler]] = {}
        self._catch_all: list[Handler] = []
        self._reader: asyncio.Task[None] | None = None
        self._pinger: asyncio.Task[None] | None = None
        self._ping_interval = 25.0
        self.connected = False

    def on(self, event: str, handler: Handler) -> None:
        """Register a handler for a named event."""
        self._handlers.setdefault(event, []).append(handler)

    def on_any(self, handler: Handler) -> None:
        """Register a handler invoked for every event as ``handler(name, *args)``."""
        self._catch_all.append(handler)

    async def connect(
        self, ip: str, port: int, path: str, query: str, *, timeout: float = 8.0
    ) -> None:
        """Open the websocket, read the EIO open handshake, join the default namespace."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        url = f"ws://{ip}:{port}{path}/?EIO=3&transport=websocket&{query}"
        _LOGGER.debug("eio3 connecting %s", url)
        # Wrap connect + handshake read in one deadline (portable across aiohttp versions).
        async with asyncio.timeout(timeout):
            self._ws = await self._session.ws_connect(url, heartbeat=None)
            # Read frames until the engineio OPEN handshake arrives (carries pingInterval).
            while True:
                msg = await self._ws.receive()
                if msg.type != aiohttp.WSMsgType.TEXT:
                    raise VaonisSocketError(f"unexpected handshake frame: {msg.type}")
                kind, payload = decode_frame(msg.data)
                _LOGGER.debug("eio3 handshake recv %s: %.200s", kind, msg.data)
                if kind == "open":
                    self._ping_interval = float(payload.get("pingInterval", 25000)) / 1000.0
                    break
        await self._ws.send_str("40")  # Socket.IO CONNECT to default namespace
        self.connected = True
        self._reader = asyncio.create_task(self._read_loop())
        self._pinger = asyncio.create_task(self._ping_loop())

    async def emit(self, event: str, *args: Any) -> None:
        """Emit a Socket.IO event on the default namespace."""
        if self._ws is None or self._ws.closed:
            raise VaonisSocketError("socket not connected")
        await self._ws.send_str(encode_event(event, list(args)))

    async def disconnect(self) -> None:
        """Tear down tasks, the websocket, and (if we created it) the session."""
        self.connected = False
        for task in (self._pinger, self._reader):
            if task is not None:
                task.cancel()
        if self._ws is not None and not self._ws.closed:
            with contextlib.suppress(Exception):  # best-effort Socket.IO disconnect
                await self._ws.send_str("41")
            await self._ws.close()
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def _ping_loop(self) -> None:
        while self.connected and self._ws is not None and not self._ws.closed:
            await asyncio.sleep(self._ping_interval)
            try:
                await self._ws.send_str("2")
            except Exception:
                break

    async def _read_loop(self) -> None:
        assert self._ws is not None
        async for msg in self._ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            kind, payload = decode_frame(msg.data)
            if kind not in ("event",):
                _LOGGER.debug("eio3 recv %s: %.200s", kind, msg.data)
            if kind == "ping":
                await self._ws.send_str("3")
            elif kind == "event":
                name, args = payload
                await self._dispatch(name, args)
        self.connected = False

    async def _dispatch(self, name: str, args: list[Any]) -> None:
        for handler in self._handlers.get(name, []):
            await self._invoke(handler, args)
        for handler in self._catch_all:
            await self._invoke(handler, [name, *args])

    @staticmethod
    async def _invoke(handler: Handler, args: list[Any]) -> None:
        result = handler(*args)
        if asyncio.iscoroutine(result):
            await result


class VaonisSocketError(Exception):
    """Engine.IO/Socket.IO transport error."""
