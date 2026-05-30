"""Engine.IO v3 / Socket.IO v2 frame codec tests (pure functions)."""

from __future__ import annotations

from pyvaonis._eio3 import decode_frame
from pyvaonis._eio3 import encode_event


def test_encode_event() -> None:
    assert encode_event("message", ["takeControl"]) == '42["message","takeControl"]'
    assert encode_event("message", ["setUserName", {"device": "d"}]) == (
        '42["message","setUserName",{"device":"d"}]'
    )


def test_decode_open_carries_ping_interval() -> None:
    kind, payload = decode_frame('0{"sid":"abc","pingInterval":25000,"pingTimeout":60000}')
    assert kind == "open"
    assert payload["pingInterval"] == 25000


def test_decode_status_event() -> None:
    kind, payload = decode_frame('42["STATUS_UPDATED",{"challenge":"xAA","bootCount":3}]')
    assert kind == "event"
    name, args = payload
    assert name == "STATUS_UPDATED"
    assert args[0]["challenge"] == "xAA"


def test_decode_ping_pong_connect() -> None:
    assert decode_frame("2")[0] == "ping"
    assert decode_frame("3")[0] == "pong"
    assert decode_frame("40")[0] == "connect"
    assert decode_frame("41")[0] == "disconnect"


def test_decode_event_with_ack_id() -> None:
    # Some events carry an ack id between the '2' and the JSON array.
    kind, payload = decode_frame('4217["EVT",{"k":1}]')
    assert kind == "event"
    name, args = payload
    assert name == "EVT"
    assert args[0]["k"] == 1
