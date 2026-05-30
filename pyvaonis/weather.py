"""Is tonight worth imaging? Cloud-cover forecast + Moon, distilled to a verdict.

Uses Open-Meteo (free, no API key) for hourly cloud cover — its explicit low/mid/high cloud
layers are what matter for astrophotography. Combines the forecast over tonight's dark window
(Sun ≤ -10°, from :mod:`pyvaonis.astro`) with the Moon (if ``ephem`` is installed) into a
simple ``good`` / ``marginal`` / ``poor`` verdict. The app itself uses OpenWeatherMap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime

import aiohttp

from . import astro

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
GOOD_CLOUD_PCT = 20.0
MARGINAL_CLOUD_PCT = 50.0


@dataclass
class HourCondition:
    """Cloud cover (%) and visibility (m) for one forecast hour (UTC)."""

    time: datetime
    cloud_cover: float
    cloud_low: float | None = None
    cloud_mid: float | None = None
    cloud_high: float | None = None
    visibility: float | None = None


@dataclass
class NightConditions:
    """Verdict for tonight's dark window."""

    verdict: str  # "good" | "marginal" | "poor" | "no_dark_window"
    reason: str
    dark_start: datetime | None = None
    dark_end: datetime | None = None
    mean_cloud: float | None = None
    max_cloud: float | None = None
    moon_illumination: float | None = None
    moon_up: bool | None = None
    hours: list[HourCondition] | None = None


def _parse_forecast(data: dict) -> list[HourCondition]:
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []

    def col(key: str) -> list:
        return hourly.get(key) or [None] * len(times)

    cc, lo, mi, hi, vis = (
        col("cloud_cover"),
        col("cloud_cover_low"),
        col("cloud_cover_mid"),
        col("cloud_cover_high"),
        col("visibility"),
    )
    out: list[HourCondition] = []
    for i, t in enumerate(times):
        # Open-Meteo returns naive ISO times in the requested timezone (we request UTC).
        when = datetime.fromisoformat(t).replace(tzinfo=UTC)
        out.append(
            HourCondition(
                time=when,
                cloud_cover=float(cc[i]) if cc[i] is not None else 0.0,
                cloud_low=lo[i],
                cloud_mid=mi[i],
                cloud_high=hi[i],
                visibility=vis[i],
            )
        )
    return out


async def cloud_forecast(
    latitude: float,
    longitude: float,
    *,
    session: aiohttp.ClientSession | None = None,
    forecast_days: int = 2,
    timeout: float = 20.0,
) -> list[HourCondition]:
    """Hourly cloud cover for the next ``forecast_days`` days (UTC)."""
    params = {
        "latitude": f"{latitude:.4f}",
        "longitude": f"{longitude:.4f}",
        "hourly": "cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,visibility",
        "timezone": "UTC",
        "forecast_days": str(forecast_days),
    }
    owns = session is None
    session = session or aiohttp.ClientSession()
    try:
        async with session.get(
            OPEN_METEO_URL, params=params, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            resp.raise_for_status()
            return _parse_forecast(await resp.json())
    finally:
        if owns:
            await session.close()


def summarize(
    hours: list[HourCondition],
    dark_start: datetime,
    dark_end: datetime,
    *,
    moon_illumination: float | None = None,
    moon_up: bool | None = None,
    good_cloud: float = GOOD_CLOUD_PCT,
    marginal_cloud: float = MARGINAL_CLOUD_PCT,
) -> NightConditions:
    """Distil forecast + Moon into a verdict over the dark window (pure; no I/O)."""
    in_window = [h for h in hours if dark_start <= h.time <= dark_end]
    if not in_window:
        return NightConditions("poor", "no forecast for the dark window", dark_start, dark_end)
    mean_cloud = sum(h.cloud_cover for h in in_window) / len(in_window)
    max_cloud = max(h.cloud_cover for h in in_window)

    if mean_cloud <= good_cloud:
        verdict, reason = "good", f"clear: ~{mean_cloud:.0f}% mean cloud (max {max_cloud:.0f}%)"
    elif mean_cloud <= marginal_cloud:
        verdict, reason = "marginal", f"partly cloudy: ~{mean_cloud:.0f}% mean cloud"
    else:
        verdict, reason = "poor", f"overcast: ~{mean_cloud:.0f}% mean cloud"

    if moon_up and moon_illumination is not None and moon_illumination >= 0.5:
        reason += f"; bright Moon up ({moon_illumination * 100:.0f}%) — hurts faint deep-sky"
    return NightConditions(
        verdict=verdict,
        reason=reason,
        dark_start=dark_start,
        dark_end=dark_end,
        mean_cloud=mean_cloud,
        max_cloud=max_cloud,
        moon_illumination=moon_illumination,
        moon_up=moon_up,
        hours=in_window,
    )


async def assess_night(
    latitude: float,
    longitude: float,
    when: datetime | None = None,
    *,
    session: aiohttp.ClientSession | None = None,
    good_cloud: float = GOOD_CLOUD_PCT,
    marginal_cloud: float = MARGINAL_CLOUD_PCT,
) -> NightConditions:
    """Assess tonight: dark window x cloud forecast x Moon -> a verdict."""
    when = when or datetime.now(UTC)
    window = astro.observing_window(latitude, longitude, when)
    if window is None:
        return NightConditions("no_dark_window", "Sun never drops below -10° in the next 24h")
    dark_start, dark_end = window

    hours = await cloud_forecast(latitude, longitude, session=session)

    moon_illum: float | None = None
    moon_up: bool | None = None
    midpoint = dark_start + (dark_end - dark_start) / 2
    try:
        moon_illum = astro.moon_illumination(midpoint)
        moon_up = astro.solar_system_altitude("moon", latitude, longitude, midpoint) > 0
    except RuntimeError:
        pass  # ephem not installed; verdict is cloud-only

    return summarize(
        hours,
        dark_start,
        dark_end,
        moon_illumination=moon_illum,
        moon_up=moon_up,
        good_cloud=good_cloud,
        marginal_cloud=marginal_cloud,
    )
