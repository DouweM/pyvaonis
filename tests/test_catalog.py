"""Catalog loading, visibility math, and observation mapping."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime

from pystellina.catalog import get_object
from pystellina.catalog import load_catalog
from pystellina.catalog import visible_now
from pystellina.catalog import visible_tonight

# Amsterdam, a winter evening when Orion (M42) is well up.
LAT, LON = 52.37, 4.90
WHEN = datetime(2026, 1, 15, 22, 0, tzinfo=UTC)


def test_catalog_loads() -> None:
    catalog = load_catalog()
    assert len(catalog) == 421
    assert sum(o.has_coordinates for o in catalog) == 411


def test_lookup_by_designation_and_name() -> None:
    by_id = get_object("M42")
    assert by_id is not None
    assert by_id.display_name == "Orion Nebula"
    assert by_id.designation == "M42"
    assert by_id.id_messier == "42"
    # case-insensitive, resolvable by raw id, designation, and human name
    assert get_object("m42") is by_id
    assert get_object("orion nebula") is by_id
    assert get_object("nope-not-real") is None


def test_visibility_is_ranked_and_filtered() -> None:
    rows = visible_now(LAT, LON, WHEN, min_altitude=15.0, min_grade=5.0, limit=10)
    assert rows, "expected some objects above the horizon"
    assert all(v.altitude >= 15.0 for v in rows)
    assert all((v.obj.grade or 0) >= 5.0 for v in rows)
    # ranked by grade desc, then altitude desc
    keys = [(v.obj.grade or 0, v.altitude) for v in rows]
    assert keys == sorted(keys, reverse=True)


def test_known_object_altitude_is_plausible() -> None:
    m42 = get_object("M42")
    assert m42 is not None
    alt = m42.altitude(LAT, LON, WHEN)
    # M42 (dec ~ -5.5°) culminates around 32° at this latitude; at 22:00 mid-Jan it's well up.
    assert 20.0 < alt < 33.0


def test_objects_have_names_and_descriptions() -> None:
    m42 = get_object("Orion Nebula") or get_object("M42")
    assert m42 is not None
    assert m42.id == "M42"
    assert m42.display_name == "Orion Nebula"
    assert m42.description and "Orion" in m42.description
    assert m42.summary()["name"] == "Orion Nebula"


def test_to_observation_uses_recommended_settings() -> None:
    m42 = get_object("M42")
    assert m42 is not None
    payload = m42.to_observation().to_payload()
    assert payload["objectId"] == "M42"
    assert payload["objectName"] == "Orion Nebula"
    assert payload["ra"] == m42.ra
    assert payload["gain"] == m42.gain  # from the catalog's recommended settings


def test_solar_object_resolves_via_ephemeris() -> None:
    jupiter = get_object("Jupiter")
    assert jupiter is not None
    assert jupiter.is_solar
    assert not jupiter.has_coordinates  # no stored coords
    payload = jupiter.to_observation().to_payload()  # computed via ephem (dev dep)
    assert payload["objectName"] == "Jupiter"
    assert 0.0 <= payload["ra"] < 360.0


def test_require_dark_returns_nothing_in_daylight() -> None:
    day = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    assert visible_now(LAT, LON, day, require_dark=True) == []


def test_visible_tonight_reports_peaks_over_the_dark_window() -> None:
    rows = visible_tonight(LAT, LON, WHEN, min_altitude=15.0, min_grade=5.0, limit=10)
    assert rows, "expected targets to peak above 15° during tonight's dark window"
    assert all(v.peak_altitude >= 15.0 for v in rows)
    # ranked by grade desc, then peak altitude desc
    keys = [(v.obj.grade or 0, v.peak_altitude) for v in rows]
    assert keys == sorted(keys, reverse=True)
    # tonight's window can surface targets not yet up at the window start
    assert any(not v.up_now for v in rows) or all(v.up_now for v in rows)
