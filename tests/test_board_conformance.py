"""Board-geometry conformance for the surf plugin.

Runs the shared suite FiestaBoard core holds its own plugins to (see
``src/plugins/geometry_conformance.py``): the plugin must render within
bounds on every board shape -- Flagship, Note, and every note_array size
from 1x1 to 8x8 notes, forwards and backwards, with no board bound at all.
"""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.plugins.geometry_conformance import assert_board_conformance

from plugins.surf import SurfPlugin

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "manifest.json"
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def _manifest() -> dict:
    with open(MANIFEST_PATH) as f:
        return json.load(f)


def _response(payload):
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = payload
    return resp


def _stub_requests_get(url, **kwargs):
    """Answer both Open-Meteo endpoints with fixed, valid data.

    The values are picked to land on ``GOOD`` quality (a middling case, not
    the shortest possible text) so the layout is exercised the same way for
    every board shape the suite renders.
    """
    if url == MARINE_URL:
        return _response(
            {
                "current": {"wave_height": 1.2, "swell_wave_period": 11.0},
                "daily": {"wave_height_max": [1.2], "swell_wave_period_max": [11.0]},
            }
        )
    if url == FORECAST_URL:
        return _response({"current": {"wind_speed_10m": 9.0, "wind_direction_10m": 270}})
    raise AssertionError(f"unexpected URL requested: {url}")


@pytest.fixture
def make_plugin(monkeypatch):
    """A factory for fresh, ready-to-render plugins with the network stubbed.

    The conformance suite calls this several times and renders each result
    across every board shape, so the stub is installed once here (for the
    fixture's duration) rather than per-call -- real network access would
    fail loudly via the ``AssertionError`` above if anything slipped through.
    """
    monkeypatch.setattr("plugins.surf.requests.get", Mock(side_effect=_stub_requests_get))

    def _make() -> SurfPlugin:
        return SurfPlugin(_manifest())

    return _make


def test_renders_on_every_board_shape(make_plugin):
    """Wave height, swell, quality and wind must fit Flagship, Note, and any note_array.

    ``strict_growth=True``: a taller board must show more than the compact
    3-line layout once that layout is full, which it is for surf conditions'
    handful of facts (the note packs 3 lines exactly; taller boards break
    into a one-fact-per-line layout with room for a plain-language summary
    too). ``require_note_array_preview=True``: the manifest now ships one.
    """
    assert_board_conformance(
        make_plugin,
        manifest=_manifest(),
        strict_growth=True,
        require_note_array_preview=True,
    )
