"""Bundled object catalog + "what to watch tonight" visibility.

The catalog (``data/catalog.json``) is extracted from the Singularity app and carries, per
object, a human name + description, coordinates (RA/Dec, J2000), magnitude, a curated
``grade`` (0-10), and the app's recommended capture settings. :func:`visible_now` filters by
altitude (and optionally darkness) so callers can offer "tonight's targets".

Fixed deep-sky objects use pure-Python geometry. Solar-system objects (planets/Moon/Sun)
carry no stored coordinates — their positions are computed on demand via :mod:`pyvaonis.astro`,
which needs the optional ``ephem`` dependency.
"""

from __future__ import annotations

import json
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from functools import cache
from importlib import resources
from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict

from . import astro
from .models import ObservationBody

_DATA = resources.files(__package__) / "data" / "catalog.json"

# Deep-sky object types that live-stack (the app's observation_rules `stackingEnabled` set); their
# per-type histogramLow default when the catalog entry doesn't carry an explicit one.
_STACKING_TYPES = {"OP", "ODC", "ODE", "ODOC"}
_HISTOGRAM_LOW_BY_TYPE = {"OP": 0.0, "ODC": -0.75, "ODE": -1.0, "ODOC": -1.0}
# Per-object solar gain/exposureMicroSec for Stellina (observation_rules.json objectId overrides);
# the catalog only carries the generic planet rule, so we apply the app's per-planet values here.
_SOLAR_PARAMS_STELLINA: dict[str, tuple[int, int]] = {
    "moon": (50, 40000),
    "venus": (50, 40000),
    "jupiter": (16, 10000),
    "saturn": (100, 100000),
    "mars": (16, 10000),
    "uranus": (200, 500000),
    "neptune": (270, 2000000),
}


class CatalogObject(BaseModel):
    """A catalog entry. Imaging/extra fields are optional and kept if present."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str | None = None
    description: str | None = None
    constellation: str | None = None
    category: str | None = None
    type: str | None = None
    ra: float | None = None  # degrees, J2000
    de: float | None = None  # degrees, J2000
    magnitude: float | None = None
    grade: float | None = None
    duration: int | None = None  # recommended minutes
    gain: int | None = None
    exposure: int | None = None  # micro-seconds
    orientation: float | None = None
    size: float | None = None
    distance: float | None = None
    distance_unit: str | None = None
    real_size: float | None = None
    real_size_unit: str | None = None
    discovered_by: str | None = None
    discovered_in: str | None = None
    trivia: str | None = None
    short_title: str | None = None
    category_label: str | None = None
    constellation_name: str | None = None
    id_messier: str | None = None
    id_ngc: str | None = None
    id_ic: str | None = None

    def __init__(self, **data: object) -> None:
        for src, dst in (
            ("idMessier", "id_messier"),
            ("idNgc", "id_ngc"),
            ("idIc", "id_ic"),
            ("distanceUnit", "distance_unit"),
            ("realSize", "real_size"),
            ("realSizeUnit", "real_size_unit"),
            ("discoveredBy", "discovered_by"),
            ("discoveredIn", "discovered_in"),
            ("shortTitle", "short_title"),
            ("categoryLabel", "category_label"),
            ("constellationName", "constellation_name"),
        ):
            if src in data:
                data.setdefault(dst, data[src])
        super().__init__(**data)

    @property
    def display_name(self) -> str:
        """Human name, falling back to designation then raw id."""
        if self.name:
            return self.name
        if self.id_messier:
            return f"M{self.id_messier}"
        if self.id_ngc:
            return f"NGC {self.id_ngc}"
        if self.id_ic:
            return f"IC {self.id_ic}"
        return self.id

    @property
    def designation(self) -> str | None:
        """Catalogue designation (M/NGC/IC), if any."""
        if self.id_messier:
            return f"M{self.id_messier}"
        if self.id_ngc:
            return f"NGC {self.id_ngc}"
        if self.id_ic:
            return f"IC {self.id_ic}"
        return None

    @property
    def is_solar(self) -> bool:
        """Whether this is a solar-system object (ephemeris-driven, no stored coords)."""
        return self.id.lower() in astro.SOLAR_SYSTEM

    @property
    def has_coordinates(self) -> bool:
        """Whether a fixed RA/Dec is stored."""
        return self.ra is not None and self.de is not None

    def coordinates(self, when: datetime | None = None) -> tuple[float, float]:
        """(RA, Dec) in degrees — stored for fixed objects, computed for solar ones."""
        if self.has_coordinates:
            assert self.ra is not None and self.de is not None
            return self.ra, self.de
        if self.is_solar:
            return astro.solar_system_radec(self.id, when)
        raise ValueError(f"{self.id} has no coordinates")

    def altitude(self, latitude: float, longitude: float, when: datetime | None = None) -> float:
        """Altitude in degrees above the horizon at a location/time (UTC)."""
        ra, dec = self.coordinates(when)
        return astro.equatorial_altitude(ra, dec, latitude, longitude, when or datetime.now(UTC))

    def to_observation(self, when: datetime | None = None) -> ObservationBody:
        """Build a start-observation body the firmware accepts, mirroring the app's derivation.

        Live-stacking (and the six histogram/background params the firmware then requires) is enabled
        only for deep-sky types (OP/ODC/ODE/ODOC); stars send ``doStacking=false`` and omit those
        params. Per-object histogram values from the catalog are used when present, else the app's
        per-type rule defaults. For solar objects the firmware resolves the coordinates itself, so —
        exactly as the app does — we send no RA/Dec/rot (the near-Sun safety guard runs in
        :meth:`VaonisClient.observe_object`).
        """
        if self.is_solar:  # planets/Moon/Sun: objectId + camera params, no coordinates, no stacking
            gain, exposure = _SOLAR_PARAMS_STELLINA.get(self.id.lower(), (self.gain, self.exposure))
            return ObservationBody(
                object_id=self.id,
                object_name=self.display_name,
                object_type=self.type or "",
                target_type="CATALOG",
                gain=gain,
                exposure_micro_sec=exposure,
                do_stacking=False,
            )

        ra, dec = self.coordinates(when)
        common: dict[str, Any] = {
            "object_id": self.id,
            "object_name": self.display_name,
            "object_type": self.type or "",
            "target_type": "CATALOG",
            "ra": ra,
            "de": dec,
            "rot": self.orientation,
            "gain": self.gain,
            "exposure_micro_sec": self.exposure,
        }
        if self.type not in _STACKING_TYPES:  # stars, untyped → single frames
            return ObservationBody(do_stacking=False, **common)

        extra = self.model_extra or {}
        low = extra.get("histogramLow")
        if low is None:
            low = _HISTOGRAM_LOW_BY_TYPE.get(self.type, 0.0)
        return ObservationBody(
            do_stacking=True,
            histogram_enabled=bool(extra.get("histogramEnabled", True)),
            histogram_low=float(low),
            histogram_medium=float(extra.get("histogramMedium", 5)),
            histogram_high=float(extra.get("histogramHigh", 0)),
            background_enabled=bool(extra.get("backgroundEnabled", True)),
            background_polyorder=float(extra.get("backgroundPolyorder", 2)),
            **common,
        )

    @staticmethod
    def _clean(value: Any) -> Any:
        """Drop the app's sentinel non-values (``N/A`` / ``?``)."""
        return None if isinstance(value, str) and value.strip() in ("N/A", "?", "") else value

    @property
    def distance_display(self) -> str | None:
        """e.g. '50,000,000 ly' (None when unknown)."""
        dist, unit = self._clean(self.distance), self._clean(self.distance_unit)
        return f"{dist:,.0f} {unit}" if dist is not None and unit else None

    @property
    def real_size_display(self) -> str | None:
        """e.g. '105,000 ly' (None when unknown)."""
        size, unit = self._clean(self.real_size), self._clean(self.real_size_unit)
        return f"{size:,.0f} {unit}" if size is not None and unit else None

    @property
    def discovery_display(self) -> str | None:
        """e.g. 'Pierre Méchain, 1781' (None when unknown)."""
        by, when = self._clean(self.discovered_by), self._clean(self.discovered_in)
        return ", ".join(str(x) for x in (by, when) if x) or None if (by or when) else None

    @property
    def trivia_facts(self) -> list[str]:
        """The app's per-object fun facts, as a clean list (its '- ' bullet lines)."""
        if not self.trivia:
            return []
        return [ln.lstrip("- ").strip() for ln in self.trivia.splitlines() if ln.strip()]

    def summary(self) -> dict[str, Any]:
        """A compact, human-facing description for CLI/API/HA attributes."""
        return {
            "id": self.id,
            "name": self.display_name,
            "designation": self.designation or self.short_title,
            "constellation": self.constellation_name or self.constellation,
            "category": self.category_label or self.category,
            "magnitude": self.magnitude,
            "grade": self.grade,
            "distance": self.distance_display,
            "real_size": self.real_size_display,
            "discovery": self.discovery_display,
            "recommended_minutes": self.duration or None,
            "is_solar": self.is_solar,
            "description": self.description,
            "trivia": self.trivia_facts,
        }


class VisibleObject(BaseModel):
    """A catalog object paired with its current altitude."""

    obj: CatalogObject
    altitude: float


class TonightObject(BaseModel):
    """A catalog object with its best (peak) altitude over tonight's dark window."""

    obj: CatalogObject
    peak_altitude: float
    peak_time: datetime  # UTC, when it peaks within the window
    up_now: bool  # already above min_altitude at the window start / now


def visibility_rating(altitude: float) -> str:
    """The app's green/orange/red observability indicator, purely from altitude (not ``grade``).

    Mirrors ``CatalogAdapter.setVisibility``: ``good`` when 20°≤alt≤80°, ``not_visible`` below 1°,
    else ``poor`` (low on the horizon or near the zenith). This is distinct from ``grade`` (the
    catalog's curated target-quality score).
    """
    if altitude < 1.0:
        return "not_visible"
    if 20.0 <= altitude <= 80.0:
        return "good"
    return "poor"


@cache
def load_catalog() -> tuple[CatalogObject, ...]:
    """Load and cache the bundled catalog."""
    raw = json.loads(_DATA.read_text(encoding="utf-8"))
    return tuple(CatalogObject(**entry) for entry in raw)


def get_object(object_id: str) -> CatalogObject | None:
    """Look up an object by id, name, or designation (case-insensitive)."""
    needle = object_id.casefold()
    for obj in load_catalog():
        names = {obj.id.casefold(), obj.display_name.casefold()}
        if obj.designation:
            names.add(obj.designation.casefold())
        if needle in names:
            return obj
    return None


def observation_object_name(store_id: str) -> str | None:
    """Friendly object name from a capture ``storeId`` (``<date>_observation_<objectId>``).

    Resolves the objectId against the catalog (so ``moon`` -> "Moon", ``M104`` -> "Sombrero
    Galaxy"); falls back to the raw id, or None when the storeId has no object part.
    """
    _, _, obj_part = store_id.partition("_observation_")
    if not obj_part:
        return None
    found = get_object(obj_part)
    return found.display_name if found else obj_part.replace("_", " ").title()


def visible_now(
    latitude: float,
    longitude: float,
    when: datetime | None = None,
    *,
    min_altitude: float = 15.0,
    min_grade: float = 0.0,
    limit: int | None = None,
    require_dark: bool = False,
    include_solar: bool = True,
) -> list[VisibleObject]:
    """Catalog objects currently above ``min_altitude``, best first.

    Ranked by curated ``grade`` (desc) then altitude (desc).

    - ``require_dark``: return nothing unless the Sun is below the app's -10° threshold.
    - ``include_solar``: include planets/Moon (skipped automatically if ``ephem`` is absent).
    """
    when = when or datetime.now(UTC)
    if require_dark and not astro.is_dark(latitude, longitude, when):
        return []

    results: list[VisibleObject] = []
    for obj in load_catalog():
        if obj.is_solar:
            if not include_solar:
                continue
            try:
                alt = obj.altitude(latitude, longitude, when)
            except RuntimeError:  # ephem not installed
                break
        elif obj.has_coordinates and (obj.grade or 0) >= min_grade:
            alt = obj.altitude(latitude, longitude, when)
        else:
            continue
        if alt >= min_altitude:
            results.append(VisibleObject(obj=obj, altitude=alt))

    results.sort(key=lambda v: (v.obj.grade or 0, v.altitude), reverse=True)
    return results[:limit] if limit is not None else results


def visible_tonight(
    latitude: float,
    longitude: float,
    when: datetime | None = None,
    *,
    min_altitude: float = 15.0,
    min_grade: float = 0.0,
    limit: int | None = None,
    include_solar: bool = True,
    samples: int = 24,
) -> list[TonightObject]:
    """Catalog objects that *will be* well-placed at any point during tonight's dark window.

    Unlike :func:`visible_now` (a single instant), this samples the Sun-below-(-10°) window
    (``astro.observing_window``) and keeps each object's peak altitude + the time it peaks, so you
    can plan "what's worth imaging tonight" even for targets that haven't risen yet. Returns []
    if there is no dark window in the next 24h. Ranked by curated ``grade`` then peak altitude.
    """
    when = when or datetime.now(UTC)
    window = astro.observing_window(latitude, longitude, when)
    if window is None:
        return []
    start, end = window
    span = (end - start).total_seconds()
    times = [start + timedelta(seconds=span * i / samples) for i in range(samples + 1)]

    results: list[TonightObject] = []
    for obj in load_catalog():
        if obj.is_solar:
            if not include_solar:
                continue
        elif not (obj.has_coordinates and (obj.grade or 0) >= min_grade):
            continue
        peak = -90.0
        peak_time = start
        try:
            for t in times:
                alt = obj.altitude(latitude, longitude, t)
                if alt > peak:
                    peak, peak_time = alt, t
        except RuntimeError:  # ephem not installed; skip solar objects
            continue
        if peak >= min_altitude:
            up_now = obj.altitude(latitude, longitude, start) >= min_altitude
            results.append(
                TonightObject(obj=obj, peak_altitude=peak, peak_time=peak_time, up_now=up_now)
            )

    results.sort(key=lambda v: (v.obj.grade or 0, v.peak_altitude), reverse=True)
    return results[:limit] if limit is not None else results
