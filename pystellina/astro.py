"""Sky geometry: sidereal time, altitude, Sun position, darkness, ephemerides.

Fixed-object math (sidereal time, altitude, Sun position) is pure-Python (Meeus,
low-precision) with no dependencies — enough for visibility and the app's darkness rule.

The app treats the night as starting when the **Sun drops below -10° altitude** (see
``GetSunDetailLifetime`` in the decompiled app), so :func:`is_dark` matches that by default.

Solar-system objects (planets/Moon/Sun) need ephemerides; those helpers require the
optional ``ephem`` dependency (``pip install "pystellina[astro]"``).
"""

from __future__ import annotations

import math
from datetime import UTC
from datetime import datetime
from datetime import timedelta

# The app's observing threshold: night = Sun altitude <= this.
DARK_SUN_ALTITUDE = -10.0

SOLAR_SYSTEM = frozenset(
    {"sun", "mercury", "venus", "moon", "mars", "jupiter", "saturn", "uranus", "neptune"}
)


def julian_date(when: datetime) -> float:
    """Julian Date for a timezone-aware datetime."""
    return when.astimezone(UTC).timestamp() / 86400.0 + 2440587.5


def local_sidereal_deg(when: datetime, longitude: float) -> float:
    """Local apparent sidereal time in degrees (east-positive longitude)."""
    jd = julian_date(when)
    d = jd - 2451545.0
    t = d / 36525.0
    gmst = 280.46061837 + 360.98564736629 * d + 0.000387933 * t * t - (t**3) / 38710000.0
    return (gmst + longitude) % 360.0


def equatorial_altitude(
    ra_deg: float, dec_deg: float, latitude: float, longitude: float, when: datetime
) -> float:
    """Geometric altitude (degrees) of an RA/Dec position at a location/time."""
    hour_angle = math.radians((local_sidereal_deg(when, longitude) - ra_deg) % 360.0)
    dec, lat = math.radians(dec_deg), math.radians(latitude)
    sin_alt = math.sin(dec) * math.sin(lat) + math.cos(dec) * math.cos(lat) * math.cos(hour_angle)
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_alt))))


def sun_position(when: datetime) -> tuple[float, float]:
    """Apparent (RA, Dec) of the Sun in degrees (Meeus, ~0.01° accuracy)."""
    t = (julian_date(when) - 2451545.0) / 36525.0
    rad = math.radians
    mean_long = 280.46646 + 36000.76983 * t + 0.0003032 * t * t
    mean_anom = 357.52911 + 35999.05029 * t - 0.0001537 * t * t
    center = (
        (1.914602 - 0.004817 * t - 0.000014 * t * t) * math.sin(rad(mean_anom))
        + (0.019993 - 0.000101 * t) * math.sin(rad(2 * mean_anom))
        + 0.000289 * math.sin(rad(3 * mean_anom))
    )
    true_long = mean_long + center
    omega = 125.04 - 1934.136 * t
    app_long = rad(true_long - 0.00569 - 0.00478 * math.sin(rad(omega)))
    obliquity = rad(23.439291 - 0.0130042 * t + 0.00256 * math.cos(rad(omega)))
    ra = math.degrees(math.atan2(math.cos(obliquity) * math.sin(app_long), math.cos(app_long)))
    dec = math.degrees(math.asin(math.sin(obliquity) * math.sin(app_long)))
    return ra % 360.0, dec


def angular_separation(ra1_deg: float, dec1_deg: float, ra2_deg: float, dec2_deg: float) -> float:
    """Great-circle angle (degrees) between two equatorial positions."""
    ra1, dec1, ra2, dec2 = map(math.radians, (ra1_deg, dec1_deg, ra2_deg, dec2_deg))
    cos_sep = math.sin(dec1) * math.sin(dec2) + math.cos(dec1) * math.cos(dec2) * math.cos(
        ra1 - ra2
    )
    return math.degrees(math.acos(max(-1.0, min(1.0, cos_sep))))


def separation_from_sun(ra_deg: float, dec_deg: float, when: datetime | None = None) -> float:
    """Angular distance (degrees) of an RA/Dec from the Sun — for solar-safety checks."""
    sun_ra, sun_dec = sun_position(when or datetime.now(UTC))
    return angular_separation(ra_deg, dec_deg, sun_ra, sun_dec)


def sun_altitude(latitude: float, longitude: float, when: datetime | None = None) -> float:
    """Geometric altitude of the Sun (degrees)."""
    when = when or datetime.now(UTC)
    ra, dec = sun_position(when)
    return equatorial_altitude(ra, dec, latitude, longitude, when)


def is_dark(
    latitude: float,
    longitude: float,
    when: datetime | None = None,
    *,
    sun_max_altitude: float = DARK_SUN_ALTITUDE,
) -> bool:
    """Whether it is dark enough to observe (Sun at/below ``sun_max_altitude``)."""
    return sun_altitude(latitude, longitude, when) <= sun_max_altitude


def observing_window(
    latitude: float,
    longitude: float,
    when: datetime | None = None,
    *,
    sun_max_altitude: float = DARK_SUN_ALTITUDE,
    step_minutes: int = 5,
) -> tuple[datetime, datetime] | None:
    """The current/next dark interval (dusk, dawn) within the next 24h, or None.

    Coarse minute-stepped scan, mirroring how the app walks the Sun's altitude.
    """
    when = (when or datetime.now(UTC)).astimezone(UTC)
    step = timedelta(minutes=step_minutes)
    horizon = when + timedelta(hours=24)

    def dark_at(t: datetime) -> bool:
        return sun_altitude(latitude, longitude, t) <= sun_max_altitude

    # Find dusk: first dark sample (now, if already dark).
    t = when
    dusk = when if dark_at(when) else None
    while dusk is None and t < horizon:
        t += step
        if dark_at(t):
            dusk = t
    if dusk is None:
        return None
    # Find dawn: first non-dark sample after dusk.
    t = dusk
    while t < horizon:
        t += step
        if not dark_at(t):
            return dusk, t
    return dusk, horizon


def _require_ephem():
    try:
        import ephem
    except ImportError as err:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "solar-system objects need the 'ephem' package: pip install 'pystellina[astro]'"
        ) from err
    return ephem


def solar_system_radec(name: str, when: datetime | None = None) -> tuple[float, float]:
    """Apparent (RA, Dec) in degrees of a planet/Moon/Sun (requires ``ephem``)."""
    ephem = _require_ephem()
    key = name.lower()
    if key not in SOLAR_SYSTEM:
        raise ValueError(f"{name!r} is not a supported solar-system object")
    body_cls = {"sun": "Sun", "moon": "Moon"}.get(key, key.capitalize())
    body = getattr(ephem, body_cls)()
    body.compute((when or datetime.now(UTC)).astimezone(UTC))
    return math.degrees(float(body.ra)), math.degrees(float(body.dec))


def solar_system_altitude(
    name: str, latitude: float, longitude: float, when: datetime | None = None
) -> float:
    """Altitude (degrees) of a planet/Moon/Sun at a location/time (requires ``ephem``)."""
    when = when or datetime.now(UTC)
    ra, dec = solar_system_radec(name, when)
    return equatorial_altitude(ra, dec, latitude, longitude, when)
