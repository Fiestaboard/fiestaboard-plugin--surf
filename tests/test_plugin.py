"""Tests for the surf plugin.

Everything here exercises ``plugins.surf`` — the code this repo ships.
The platform's ``src/utils/surf.py`` was a pre-extraction leftover and is
gone; nothing below imports from ``src.utils``.
"""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from plugins.surf import DEFAULT_LAT, DEFAULT_LON, Plugin, SurfPlugin

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "manifest.json"

MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def _manifest():
    with open(MANIFEST_PATH) as f:
        return json.load(f)


def _response(payload):
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = payload
    return resp


def _marine(wave_m, period_s, current_wave_m=None, current_period_s=None):
    return {
        "current": {
            "wave_height": current_wave_m if current_wave_m is not None else wave_m,
            "swell_wave_period": current_period_s if current_period_s is not None else period_s,
        },
        "daily": {"wave_height_max": [wave_m], "swell_wave_period_max": [period_s]},
    }


def _wind(speed_mph, direction_deg):
    return {"current": {"wind_speed_10m": speed_mph, "wind_direction_10m": direction_deg}}


def _route_by_url(marine=None, wind=None):
    """A ``requests.get`` side effect that answers each Open-Meteo API by URL."""

    def side_effect(url, **kwargs):
        if url == MARINE_URL:
            if marine is None:
                raise Exception("no marine response scripted")
            return _response(marine)
        if url == FORECAST_URL:
            if wind is None:
                raise Exception("no wind response scripted")
            return _response(wind)
        raise AssertionError(f"unexpected URL requested: {url}")

    return side_effect


@pytest.fixture
def plugin():
    return SurfPlugin(_manifest())


class TestPluginConstruction:
    """What the platform does with this package: import ``Plugin`` and build it."""

    def test_module_exports_the_plugin_class(self):
        assert Plugin is SurfPlugin

    def test_plugin_id_matches_manifest(self, plugin):
        assert plugin.plugin_id == _manifest()["id"] == "surf"

    def test_validate_config_accepts_empty_config(self, plugin):
        """No settings are required — the defaults are a valid location."""
        assert plugin.validate_config({}) == []

    def test_validate_config_accepts_valid_coordinates(self, plugin):
        assert plugin.validate_config({"latitude": 37.0, "longitude": -122.0}) == []

    def test_validate_config_rejects_bad_latitude(self, plugin):
        errors = plugin.validate_config({"latitude": 100, "longitude": 0})
        assert errors == ["Latitude must be between -90 and 90"]

    def test_validate_config_rejects_bad_longitude(self, plugin):
        errors = plugin.validate_config({"latitude": 0, "longitude": 200})
        assert errors == ["Longitude must be between -180 and 180"]


class TestSurfQuality:
    """``_calculate_quality``: EXCELLENT > GOOD > FAIR > POOR."""

    def test_excellent_high_period_low_wind(self, plugin):
        assert plugin._calculate_quality(15, 5) == ("EXCELLENT", "GREEN")

    def test_excellent_just_inside_thresholds(self, plugin):
        assert plugin._calculate_quality(12.1, 11.9) == ("EXCELLENT", "GREEN")

    def test_period_exactly_12_is_not_excellent(self, plugin):
        assert plugin._calculate_quality(12.0, 5) == ("GOOD", "YELLOW")

    def test_wind_exactly_12_is_not_excellent(self, plugin):
        assert plugin._calculate_quality(15, 12.0) == ("GOOD", "YELLOW")

    def test_good_moderate_conditions(self, plugin):
        assert plugin._calculate_quality(11, 14) == ("GOOD", "YELLOW")

    def test_good_just_above_period_threshold(self, plugin):
        assert plugin._calculate_quality(10.1, 14) == ("GOOD", "YELLOW")

    def test_fair_decent_swell_despite_wind(self, plugin):
        assert plugin._calculate_quality(9, 18) == ("FAIR", "ORANGE")

    def test_fair_low_wind_despite_short_period(self, plugin):
        assert plugin._calculate_quality(5, 15) == ("FAIR", "ORANGE")

    def test_poor_short_period_and_high_wind(self, plugin):
        assert plugin._calculate_quality(5, 25) == ("POOR", "RED")

    def test_poor_at_exact_boundaries(self, plugin):
        assert plugin._calculate_quality(8, 20) == ("POOR", "RED")

    def test_zero_values_are_fair(self, plugin):
        assert plugin._calculate_quality(0, 0) == ("FAIR", "ORANGE")

    def test_good_to_excellent_boundary(self, plugin):
        assert plugin._calculate_quality(11.9, 5) == ("GOOD", "YELLOW")
        assert plugin._calculate_quality(12.1, 5) == ("EXCELLENT", "GREEN")

    def test_fair_to_poor_boundary(self, plugin):
        assert plugin._calculate_quality(8.1, 25) == ("FAIR", "ORANGE")
        assert plugin._calculate_quality(7.9, 20.1) == ("POOR", "RED")

    def test_high_wind_downgrades_excellent_swell(self, plugin):
        assert plugin._calculate_quality(18, 13) == ("GOOD", "YELLOW")

    def test_negative_values_are_fair(self, plugin):
        assert plugin._calculate_quality(-1, -1) == ("FAIR", "ORANGE")


class TestCardinalDirections:
    @pytest.mark.parametrize(
        "degrees,expected",
        [
            (0, "N"),
            (360, "N"),
            (45, "NE"),
            (90, "E"),
            (135, "SE"),
            (180, "S"),
            (225, "SW"),
            (270, "W"),
            (315, "NW"),
        ],
    )
    def test_compass_points(self, plugin, degrees, expected):
        assert plugin._degrees_to_cardinal(degrees) == expected

    def test_rounds_to_nearest_point(self, plugin):
        assert plugin._degrees_to_cardinal(22) == "N"
        assert plugin._degrees_to_cardinal(23) == "NE"


class TestLocation:
    """Where the plugin asks for data when a location is (not) configured."""

    def test_default_location_is_ocean_beach(self, plugin):
        plugin.config = {}
        with patch("plugins.surf.requests.get", side_effect=_route_by_url(_marine(1, 1), _wind(0, 0))) as get:
            plugin.fetch_data()
        for call in get.call_args_list:
            params = call.kwargs["params"]
            assert (params["latitude"], params["longitude"]) == (DEFAULT_LAT, DEFAULT_LON)
        assert (DEFAULT_LAT, DEFAULT_LON) == (37.7599, -122.5121)

    def test_configured_location_is_sent_to_both_apis(self, plugin):
        plugin.config = {"latitude": 34.0, "longitude": -118.0}
        with patch("plugins.surf.requests.get", side_effect=_route_by_url(_marine(1, 1), _wind(0, 0))) as get:
            plugin.fetch_data()
        urls = {call.args[0] for call in get.call_args_list}
        assert urls == {MARINE_URL, FORECAST_URL}
        for call in get.call_args_list:
            params = call.kwargs["params"]
            assert (params["latitude"], params["longitude"]) == (34.0, -118.0)


class TestUpstreamFetches:
    def test_marine_request_asks_for_current_and_daily_swell(self, plugin):
        with patch("plugins.surf.requests.get", return_value=_response(_marine(1.5, 12.0))) as get:
            result = plugin._fetch_marine_data()
        assert result == _marine(1.5, 12.0)
        params = get.call_args.kwargs["params"]
        assert params["current"] == "wave_height,swell_wave_period"
        assert params["daily"] == "wave_height_max,swell_wave_period_max"
        assert params["forecast_days"] == 1

    def test_marine_network_error_is_swallowed(self, plugin):
        with patch("plugins.surf.requests.get", side_effect=Exception("API error")):
            assert plugin._fetch_marine_data() is None

    def test_wind_request_asks_for_mph(self, plugin):
        with patch("plugins.surf.requests.get", return_value=_response(_wind(10.0, 180))) as get:
            result = plugin._fetch_wind_data()
        assert result == {"wind_speed_mph": 10.0, "wind_direction": 180}
        assert get.call_args.kwargs["params"]["wind_speed_unit"] == "mph"

    def test_wind_network_error_is_swallowed(self, plugin):
        with patch("plugins.surf.requests.get", side_effect=Exception("API error")):
            assert plugin._fetch_wind_data() is None


class TestFetchData:
    """``fetch_data`` turns the two Open-Meteo responses into the template payload."""

    def test_excellent_conditions(self, plugin):
        side_effect = _route_by_url(
            marine=_marine(1.8, 15.0, current_wave_m=1.5, current_period_s=14.0),
            wind=_wind(8.0, 270),
        )
        with patch("plugins.surf.requests.get", side_effect=side_effect):
            result = plugin.fetch_data()

        assert result.available
        assert result.data == {
            "wave_height": 5.9,  # daily max 1.8 m, not the current 1.5 m
            "swell_period": 15.0,  # daily max, not the current 14 s
            "quality": "EXCELLENT",
            "quality_color": "{66}",
            "wind_speed": 8.0,
            "wind_direction": "W",
            "formatted": "SURF: 5.9ft @ 15s",
        }

    def test_poor_conditions(self, plugin):
        side_effect = _route_by_url(marine=_marine(1.2, 6.0), wind=_wind(25.0, 315))
        with patch("plugins.surf.requests.get", side_effect=side_effect):
            result = plugin.fetch_data()

        assert result.available
        assert result.data["quality"] == "POOR"
        assert result.data["quality_color"] == "{63}"
        assert result.data["wind_direction"] == "NW"
        assert result.data["formatted"] == "SURF: 3.9ft @ 6s"

    def test_payload_keys_match_manifest_variables(self, plugin):
        """Every variable the manifest declares is produced, and nothing else."""
        side_effect = _route_by_url(marine=_marine(1.0, 10.0), wind=_wind(5.0, 90))
        with patch("plugins.surf.requests.get", side_effect=side_effect):
            data = plugin.fetch_data().data
        assert set(data) == set(_manifest()["variables"]["simple"])

    def test_falls_back_to_current_when_daily_is_missing(self, plugin):
        marine = {"current": {"wave_height": 1.5, "swell_wave_period": 10.0}, "daily": {}}
        with patch("plugins.surf.requests.get", side_effect=_route_by_url(marine=marine, wind=_wind(0, 0))):
            result = plugin.fetch_data()
        assert result.available
        assert result.data["wave_height"] == 4.9
        assert result.data["swell_period"] == 10.0

    def test_wind_failure_degrades_to_calm(self, plugin):
        """The wind API is optional: without it the plugin reports 0 mph from N."""
        with patch("plugins.surf.requests.get", side_effect=_route_by_url(marine=_marine(1.8, 15.0))):
            result = plugin.fetch_data()
        assert result.available
        assert result.data["wind_speed"] == 0
        assert result.data["wind_direction"] == "N"
        assert result.data["quality"] == "EXCELLENT"

    def test_marine_failure_is_unavailable(self, plugin):
        with patch("plugins.surf.requests.get", side_effect=Exception("Network error")):
            result = plugin.fetch_data()
        assert not result.available
        assert result.data is None
        assert result.error == "Failed to fetch surf data"

    def test_unexpected_response_shape_does_not_raise(self, plugin):
        with patch("plugins.surf.requests.get", return_value=_response({"invalid": "data"})):
            result = plugin.fetch_data()
        assert result.available
        assert result.data["wave_height"] == 0
        assert result.data["swell_period"] == 0
        assert result.data["formatted"] == "SURF: 0.0ft @ 0s"

    def test_processing_error_is_reported(self, plugin):
        marine = {"current": {}, "daily": {"wave_height_max": ["not_a_number"]}}
        with patch("plugins.surf.requests.get", side_effect=_route_by_url(marine=marine, wind=_wind(0, 0))):
            result = plugin.fetch_data()
        assert not result.available
        assert result.error


class TestFormattedDisplay:
    def test_renders_six_rows(self, plugin):
        side_effect = _route_by_url(marine=_marine(0.6, 12.0), wind=_wind(8.0, 270))
        with patch("plugins.surf.requests.get", side_effect=side_effect):
            lines = plugin.get_formatted_display()

        assert lines is not None
        assert len(lines) == 6
        assert lines[0].strip() == "SURF CONDITIONS"
        assert lines[2].strip() == "WAVES: 2.0ft"
        assert lines[3].strip() == "SWELL: 12.0s"
        assert lines[4].strip() == "QUALITY: GOOD"
        assert lines[5].strip() == "WIND: 8.0mph W"
        assert all(len(line) <= 22 for line in lines)

    def test_returns_none_when_fetch_fails(self, plugin):
        with patch("plugins.surf.requests.get", side_effect=Exception("down")):
            assert plugin.get_formatted_display() is None


class TestManifestMetadata:
    """Tests for the rich metadata format in the manifest."""

    def test_manifest_uses_dict_simple_format(self):
        simple = _manifest()["variables"]["simple"]
        assert isinstance(simple, dict), "simple should use the rich dict format"

    def test_all_variables_have_descriptions(self):
        simple = _manifest()["variables"]["simple"]
        for var_name, meta in simple.items():
            assert "description" in meta and meta["description"], (
                f"Variable '{var_name}' missing description"
            )

    def test_all_variables_have_valid_groups(self):
        manifest = _manifest()
        groups = set(manifest["variables"].get("groups", {}).keys())
        for var_name, meta in manifest["variables"]["simple"].items():
            group = meta.get("group", "")
            if group:
                assert group in groups, (
                    f"Variable '{var_name}' references undefined group '{group}'"
                )

    def test_groups_are_defined(self):
        groups = _manifest()["variables"].get("groups", {})
        assert len(groups) > 0, "Manifest should define at least one group"
        for group_id, group_def in groups.items():
            assert "label" in group_def, f"Group '{group_id}' missing label"
