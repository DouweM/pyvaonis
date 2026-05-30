"""Sequencer behaviour, against a fake client (no telescope, no waiting)."""

from __future__ import annotations

from pystellina.sequence import SequenceEvent
from pystellina.sequence import SequenceItem
from pystellina.sequence import run_sequence


def test_parse_spec() -> None:
    assert SequenceItem.parse("M42:30") == SequenceItem("M42", 30.0)
    assert SequenceItem.parse("Jupiter") == SequenceItem("Jupiter", 20.0)


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.status = None  # _wait_idle treats None as "idle"

    async def take_control(self) -> None:
        self.calls.append("take_control")

    async def observe_object(self, target: str, *, replace: bool = False) -> dict:
        self.calls.append(f"observe:{target}")
        return {"success": True}

    def current_observation(self):
        return None

    async def stop_observation(self) -> dict:
        self.calls.append("stop")
        return {"success": True}

    async def park(self) -> dict:
        self.calls.append("park")
        return {"success": True}

    async def request_shutdown(self, *, force: bool = False) -> dict:
        self.calls.append("shutdown")
        return {"success": True}


async def test_runs_targets_then_parks_and_shuts_down() -> None:
    client = FakeClient()
    events: list[SequenceEvent] = []
    items = [SequenceItem("M42", 0.0), SequenceItem("M51", 0.0)]  # 0 min => no waiting

    await run_sequence(
        client,  # type: ignore[arg-type]
        items,
        latitude=52.37,
        longitude=4.90,
        require_dark=False,  # don't depend on real Sun position in the test
        park_at_end=True,
        shutdown_at_end=True,
        on_event=events.append,
    )

    assert client.calls == [
        "take_control",
        "observe:M42",
        "observe:M51",
        "stop",
        "park",
        "shutdown",
    ]
    assert [e.kind for e in events] == ["started", "finished", "started", "finished", "done"]


async def test_failed_target_is_skipped_not_fatal() -> None:
    client = FakeClient()

    async def boom(target: str, *, replace: bool = False) -> dict:
        client.calls.append(f"observe:{target}")
        raise RuntimeError("unknown object")

    client.observe_object = boom  # type: ignore[method-assign]
    events: list[SequenceEvent] = []

    await run_sequence(
        client,  # type: ignore[arg-type]
        [SequenceItem("BadTarget", 0.0)],
        latitude=52.37,
        longitude=4.90,
        require_dark=False,
        park_at_end=False,
        on_event=events.append,
    )

    assert "stop" in client.calls
    assert any(e.kind == "skipped" for e in events)
