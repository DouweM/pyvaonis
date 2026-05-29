"""Bundled object catalog + "what to watch tonight" visibility.

The catalog (``data/catalog.json``) is extracted from the Singularity app and carries, per
object, a human name + description, coordinates (RA/Dec, J2000), magnitude, a curated
``grade`` (0-10), and the app's recommended capture settings. :func:`visible_now` filters by
altitude (and optionally darkness) so callers can offer "tonight's targets".

Fixed deep-sky objects use pure-Python geometry. Solar-system objects (planets/Moon/Sun)
carry no stored coordinates — their positions are computed on demand via :mod:`pystellina.astro`,
which needs the optional ``ephem`` dependency.
"""

from __future__ import annotations

import json
from datetime import UTC
from datetime import datetime
from functools import cache
from importlib import resources
from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict

from . import astro
from .models import ObservationBody

_DATA = resources.files(__package__) / "data" / "catalog.json"


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
    id_messier: str | None = None
    id_ngc: str | None = None
    id_ic: str | None = None

    def __init__(self, **data: object) -> None:
        for src, dst in (
            ("idMessier", "id_messier"),
            ("idNgc", "id_ngc"),
            ("idIc", "id_ic"),
            ("distanceUnit", "distance_unit"),
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
        """Build a start-observation body from the recommended settings.

        For solar objects the current ephemeris RA/Dec is resolved (requires ``ephem``).
        """
        ra, dec = self.coordinates(when)
        return ObservationBody(
            object_id=self.id,
            object_name=self.display_name,
            object_type=self.type or "",
            ra=ra,
            de=dec,
            rot=self.orientation,
            gain=self.gain,
            exposure_micro_sec=self.exposure,
        )

    def summary(self) -> dict[str, Any]:
        """A compact, human-facing description for CLI/API/HA attributes."""
        return {
            "id": self.id,
            "name": self.display_name,
            "designation": self.designation,
            "constellation": self.constellation,
            "category": self.category,
            "magnitude": self.magnitude,
            "grade": self.grade,
            "distance": self.distance,
            "distance_unit": self.distance_unit,
            "recommended_minutes": self.duration,
            "is_solar": self.is_solar,
            "description": self.description,
        }


class VisibleObject(BaseModel):
    """A catalog object paired with its current altitude."""

    obj: CatalogObject
    altitude: float


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
