"""Surf Conditions plugin for FiestaBoard.

Displays surf conditions using Open-Meteo Marine API.
"""

from typing import Any, Dict, List, Optional
import logging
import requests

from src.plugins.base import PluginBase, PluginResult

logger = logging.getLogger(__name__)

# Default: Ocean Beach, San Francisco
DEFAULT_LAT = 37.7599
DEFAULT_LON = -122.5121


def _quality_note(quality: str, wide: bool) -> str:
    """One extra line of colour on conditions, shown only when a board has
    room to spare (see ``_build_display_lines``). Two lengths so it fits a
    narrow board too, once there is enough height for it to appear at all.
    """
    notes = {
        "EXCELLENT": "GREAT DAY TO SURF" if wide else "GREAT DAY",
        "GOOD": "GOOD DAY TO SURF" if wide else "GOOD DAY",
        "FAIR": "SURFABLE CONDITIONS" if wide else "SURFABLE",
        "POOR": "CONSIDER WAITING" if wide else "TOO ROUGH",
    }
    return notes.get(quality, "")


def _labeled_facts(data: Dict[str, Any], wide: bool) -> List[str]:
    """One surf fact per line, most important first.

    ``wide`` spells labels out in full when the board has the columns to
    spare; narrow boards get the abbreviated form instead so the value still
    fits -- "more cols => longer labels", not "more cols => same label,
    more padding".
    """
    wave, swell = data["wave_height"], data["swell_period"]
    quality, wind, direction = data["quality"], data["wind_speed"], data["wind_direction"]
    if wide:
        facts = [
            f"WAVE HEIGHT: {wave}FT",
            f"SWELL PERIOD: {swell}S",
            f"QUALITY: {quality}",
            f"WIND SPEED: {wind}MPH {direction}",
        ]
    else:
        facts = [
            f"WAVES {wave}FT",
            f"SWELL {swell}S",
            f"QUAL {quality}",
            f"WIND {wind}MPH {direction}",
        ]
    note = _quality_note(quality, wide)
    if note:
        facts.append(note)
    return facts


def _packed_lines(data: Dict[str, Any], rows: int) -> List[str]:
    """Compact layout for boards too short for one fact per line (a Note).

    Combines related facts onto shared lines instead of a one-fact-per-line
    layout that would not fit -- "fewer rows => abbreviate", not "fewer rows
    => truncate whatever didn't fit".
    """
    candidates = [
        f"{data['quality']} SURF",
        f"{data['wave_height']}FT {data['swell_period']}S",
        f"{data['wind_speed']}MPH {data['wind_direction']}",
    ]
    return candidates[: max(rows, 0)]


def _fit_center(text: str, width: int) -> str:
    """Center *text* in *width* characters, truncating if it still overflows.

    A defensive backstop, not the primary sizing strategy: the labels above
    are chosen to fit each board's columns, but this guarantees the hard
    "never wider than the board" bound even for an unexpectedly large value.
    """
    if width <= 0:
        return ""
    if text == "":
        return ""
    if len(text) > width:
        return text[:width]
    return text.center(width)


def _build_display_lines(data: Dict[str, Any], rows: int, cols: int) -> List[str]:
    """Lay surf conditions out for a board of *rows* x *cols*.

    Every dimension here comes from the board, never a literal: more rows
    means more facts (reflow), more columns means longer labels (reflow),
    and a board too short for a title-plus-facts layout gets the compact
    packed layout instead of an overflowing one.
    """
    if rows <= 0 or cols <= 0:
        return []

    wide = cols >= 20

    if rows < 5:
        lines = _packed_lines(data, rows)
    else:
        facts = _labeled_facts(data, wide)
        title = "SURF CONDITIONS" if wide else "SURF"
        if rows >= len(facts) + 2:
            lines = [title, ""] + facts
        elif rows >= len(facts) + 1:
            lines = [title] + facts
        else:
            lines = facts[:rows]

    return [_fit_center(line, cols) for line in lines]


class SurfPlugin(PluginBase):
    """Surf conditions plugin.

    Fetches wave height, swell period, and wind data from Open-Meteo.
    """

    # Quality thresholds
    EXCELLENT_PERIOD = 12
    EXCELLENT_WIND = 12
    GOOD_PERIOD = 10
    GOOD_WIND = 15
    
    def __init__(self, manifest: Dict[str, Any]):
        """Initialize the surf plugin."""
        super().__init__(manifest)
    
    @property
    def plugin_id(self) -> str:
        return "surf"
    
    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        """Validate surf configuration."""
        errors = []
        
        lat = config.get("latitude", DEFAULT_LAT)
        lon = config.get("longitude", DEFAULT_LON)
        
        if not (-90 <= lat <= 90):
            errors.append("Latitude must be between -90 and 90")
        if not (-180 <= lon <= 180):
            errors.append("Longitude must be between -180 and 180")
        
        return errors
    
    def _fetch_marine_data(self) -> Optional[Dict]:
        """Fetch marine data from Open-Meteo."""
        lat = self.config.get("latitude", DEFAULT_LAT)
        lon = self.config.get("longitude", DEFAULT_LON)
        
        url = "https://marine-api.open-meteo.com/v1/marine"
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "wave_height,swell_wave_period",
            "daily": "wave_height_max,swell_wave_period_max",
            "wind_speed_unit": "mph",
            "timezone": "America/Los_Angeles",
            "forecast_days": 1
        }
        
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to fetch marine data: {e}")
            return None
    
    def _fetch_wind_data(self) -> Optional[Dict]:
        """Fetch wind data from Open-Meteo Weather API."""
        lat = self.config.get("latitude", DEFAULT_LAT)
        lon = self.config.get("longitude", DEFAULT_LON)
        
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "wind_speed_10m,wind_direction_10m",
            "wind_speed_unit": "mph",
            "timezone": "America/Los_Angeles"
        }
        
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            current = data.get("current", {})
            return {
                "wind_speed_mph": current.get("wind_speed_10m", 0),
                "wind_direction": current.get("wind_direction_10m", 0)
            }
        except Exception as e:
            logger.warning(f"Failed to fetch wind data: {e}")
            return None
    
    def _calculate_quality(self, swell_period: float, wind_speed: float) -> tuple:
        """Calculate surf quality."""
        if swell_period > self.EXCELLENT_PERIOD and wind_speed < self.EXCELLENT_WIND:
            return "EXCELLENT", "GREEN"
        if swell_period > self.GOOD_PERIOD and wind_speed < self.GOOD_WIND:
            return "GOOD", "YELLOW"
        if swell_period > 8 or wind_speed < 20:
            return "FAIR", "ORANGE"
        return "POOR", "RED"
    
    def _degrees_to_cardinal(self, degrees: float) -> str:
        """Convert degrees to cardinal direction."""
        directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        index = round(degrees / 45) % 8
        return directions[index]
    
    def fetch_data(self) -> PluginResult:
        """Fetch surf conditions."""
        marine_data = self._fetch_marine_data()
        wind_data = self._fetch_wind_data()
        
        if not marine_data:
            return PluginResult(
                available=False,
                error="Failed to fetch surf data"
            )
        
        try:
            current = marine_data.get("current", {})
            daily = marine_data.get("daily", {})
            
            # Get wave height (prefer daily max)
            if daily.get("wave_height_max"):
                wave_height_m = daily["wave_height_max"][0] or 0
            else:
                wave_height_m = current.get("wave_height", 0) or 0
            
            wave_height_ft = round(wave_height_m * 3.28084, 1)
            
            # Get swell period (prefer daily max)
            if daily.get("swell_wave_period_max"):
                swell_period = daily["swell_wave_period_max"][0] or 0
            else:
                swell_period = current.get("swell_wave_period", 0) or 0
            
            # Get wind data
            wind_speed = wind_data.get("wind_speed_mph", 0) if wind_data else 0
            wind_dir = wind_data.get("wind_direction", 0) if wind_data else 0
            
            # Calculate quality
            quality, quality_color = self._calculate_quality(swell_period, wind_speed)
            
            # Color code mapping
            color_codes = {"GREEN": 66, "YELLOW": 65, "ORANGE": 64, "RED": 63}
            
            data = {
                "wave_height": wave_height_ft,
                "swell_period": round(swell_period, 1),
                "quality": quality,
                "quality_color": f"{{{color_codes.get(quality_color, 66)}}}",
                "wind_speed": round(wind_speed, 1),
                "wind_direction": self._degrees_to_cardinal(wind_dir),
                "formatted": f"SURF: {wave_height_ft}ft @ {int(swell_period)}s",
            }

            # self.board is bound (by PluginBase.get_data) for the duration of
            # this call; None outside a board-scoped render (unit tests,
            # legacy callers), which is treated as a Flagship per the plugin
            # contract -- never crash, never assume the caller bound a board.
            board = self.board
            rows = board.rows if board else 6
            cols = board.cols if board else 22

            return PluginResult(
                available=True,
                data=data,
                formatted_lines=_build_display_lines(data, rows, cols),
            )
            
        except Exception as e:
            logger.exception("Error processing surf data")
            return PluginResult(
                available=False,
                error=str(e)
            )
    
    def get_formatted_display(self) -> Optional[List[str]]:
        """Return the board-adaptive surf display.

        Delegates to the same layout ``fetch_data`` already computed for
        ``formatted_lines`` (from the same ``self.board``) rather than
        re-implementing it, so this documented-but-never-called hook can
        never drift out of sync with the path the platform actually renders.
        """
        result = self.fetch_data()
        if not result.available or not result.data:
            return None

        return result.formatted_lines


# Export the plugin class
Plugin = SurfPlugin

