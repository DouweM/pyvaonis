"""Sun position, darkness, and ephemeris tests."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime

from pystellina import astro

LAT, LON = 52.37, 4.90  # Amsterdam
NIGHT = datetime(2026, 1, 15, 22, 0, tzinfo=UTC)  # ~23:00 local, deep winter night
DAY = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)  # ~13:00 local, Sun up


def test_sun_below_horizon_at_night_above_during_day() -> None:
    assert astro.sun_altitude(LAT, LON, NIGHT) < -10.0
    assert astro.sun_altitude(LAT, LON, DAY) > 0.0


def test_is_dark_matches_minus_ten_threshold() -> None:
    assert astro.is_dark(LAT, LON, NIGHT) is True
    assert astro.is_dark(LAT, LON, DAY) is False


def test_observing_window_contains_a_dark_night() -> None:
    window = astro.observing_window(LAT, LON, NIGHT)
    assert window is not None
    dusk, dawn = window
    assert dusk <= NIGHT < dawn


def test_solar_system_position_and_altitude() -> None:
    ephem = __import__("importlib").util.find_spec("ephem")
    assert ephem is not None, "ephem is a dev dependency"
    ra, dec = astro.solar_system_radec("jupiter", NIGHT)
    assert 0.0 <= ra < 360.0
    assert -90.0 <= dec <= 90.0
    alt = astro.solar_system_altitude("moon", LAT, LON, NIGHT)
    assert -90.0 <= alt <= 90.0
