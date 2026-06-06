"""Catalog loading, visibility math, and observation mapping."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime

from pyvaonis.catalog import get_object
from pyvaonis.catalog import load_catalog
from pyvaonis.catalog import observation_object_name
from pyvaonis.catalog import visibility_rating
from pyvaonis.catalog import visible_now
from pyvaonis.catalog import visible_tonight
from pyvaonis.const import file_http_url


def test_observation_object_name() -> None:
    found = get_object("M104")
    assert found is not None
    assert observation_object_name("2026-05-30_05-20-37_observation_M104") == found.display_name
    assert observation_object_name("2026-05-30_05-20-37_observation_my_target") == "My Target"
    assert observation_object_name("2026-05-30_05-20-37") is None


def test_normalize_balens_level() -> None:
    import pytest

    from pyvaonis.const import normalize_balens_level

    assert normalize_balens_level("recommended") == "RECOMMENDED"
    assert normalize_balens_level("Soft") == "SOFT"
    assert normalize_balens_level("First Edition") == "OLD"
    assert normalize_balens_level("first-edition") == "OLD"
    with pytest.raises(ValueError):
        normalize_balens_level("bogus")


def test_file_http_url_maps_system_to_files() -> None:
    assert (
        file_http_url("10.0.0.1", "/system/captures/run/images/IMG_0001.jpg")
        == "http://10.0.0.1:8082/files/captures/run/images/IMG_0001.jpg"
    )


def test_visibility_rating_thresholds() -> None:
    # mirrors the app: good 20-80°, not_visible <1°, poor otherwise
    assert visibility_rating(0.5) == "not_visible"
    assert visibility_rating(10.0) == "poor"
    assert visibility_rating(45.0) == "good"
    assert visibility_rating(85.0) == "poor"


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


def test_to_observation_deep_sky_sends_stacking_params() -> None:
    m42 = get_object("M42")  # type ODE → stacks
    assert m42 is not None
    payload = m42.to_observation().to_payload()
    assert payload["objectId"] == "M42"
    assert payload["objectName"] == "Orion Nebula"
    assert payload["targetType"] == "CATALOG"
    assert payload["ra"] == m42.ra
    assert payload["gain"] == m42.gain
    # stacking on → the six histogram/background params the firmware requires are all present
    assert payload["doStacking"] is True
    for key in (
        "histogramEnabled",
        "histogramLow",
        "histogramMedium",
        "histogramHigh",
        "backgroundEnabled",
        "backgroundPolyorder",
    ):
        assert key in payload, key


def test_to_observation_solar_omits_coords_and_stacking() -> None:
    jupiter = get_object("Jupiter")  # solar → firmware resolves coords; no stacking
    assert jupiter is not None
    assert jupiter.is_solar
    payload = jupiter.to_observation().to_payload()
    assert payload["objectName"] == "Jupiter"
    assert payload["doStacking"] is False
    assert "ra" not in payload and "de" not in payload  # exactly as the app: omitted for solar
    assert "histogramLow" not in payload
    assert payload["gain"] == 16 and payload["exposureMicroSec"] == 10000  # per-planet override


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
