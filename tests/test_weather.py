"""Cloud-forecast parsing and the night verdict (pure functions)."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime

from pystellina.weather import HourCondition
from pystellina.weather import _parse_forecast
from pystellina.weather import summarize

SAMPLE = {
    "hourly": {
        "time": ["2026-01-15T21:00", "2026-01-15T22:00", "2026-01-15T23:00"],
        "cloud_cover": [5, 10, 80],
        "cloud_cover_low": [0, 0, 60],
        "cloud_cover_mid": [5, 10, 20],
        "cloud_cover_high": [0, 0, 0],
        "visibility": [24000, 24000, 12000],
    }
}


def test_parse_forecast() -> None:
    hours = _parse_forecast(SAMPLE)
    assert len(hours) == 3
    assert hours[0].time == datetime(2026, 1, 15, 21, 0, tzinfo=UTC)
    assert hours[2].cloud_cover == 80.0
    assert hours[2].cloud_low == 60


def _hours(*pairs: tuple[int, float]) -> list[HourCondition]:
    return [
        HourCondition(time=datetime(2026, 1, 15, h, 0, tzinfo=UTC), cloud_cover=c) for h, c in pairs
    ]


def test_verdict_good_when_clear() -> None:
    start, end = datetime(2026, 1, 15, 21, tzinfo=UTC), datetime(2026, 1, 15, 23, tzinfo=UTC)
    night = summarize(_hours((21, 5), (22, 10), (23, 15)), start, end)
    assert night.verdict == "good"
    assert night.mean_cloud == 10.0


def test_verdict_poor_when_overcast() -> None:
    start, end = datetime(2026, 1, 15, 21, tzinfo=UTC), datetime(2026, 1, 15, 23, tzinfo=UTC)
    night = summarize(_hours((21, 90), (22, 85), (23, 95)), start, end)
    assert night.verdict == "poor"


def test_bright_moon_is_noted_but_not_fatal() -> None:
    start, end = datetime(2026, 1, 15, 21, tzinfo=UTC), datetime(2026, 1, 15, 23, tzinfo=UTC)
    night = summarize(_hours((21, 5), (22, 5)), start, end, moon_illumination=0.9, moon_up=True)
    assert night.verdict == "good"  # clear skies still good
    assert "Moon" in night.reason
