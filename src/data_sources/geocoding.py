"""Geocode location names to coordinates."""
from dataclasses import dataclass
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
from geopy.location import Location
from timezonefinder import TimezoneFinder
from functools import lru_cache

_tz_finder = TimezoneFinder()

@dataclass
class GeocodedLocation:
    """Result of geocoding a location string."""
    query: str
    display_name: str
    lat: float
    lon: float


# Nominatim requires a user agent identifying the application
_geocoder = Nominatim(user_agent="severe-weather-event-reporter")

@lru_cache(maxsize=128)
def geocode(location: str) -> GeocodedLocation | None:
    """Convert a location string to coordinates and a display name using OSM Nominatim.
    
    Returns the highest-importance result for the given location. 
    Returns none if geocoding fails or no results are found.
    """
    try:
        results = _geocoder.geocode(
            location,
            country_codes="us",
            exactly_one=False,  # get multiple candidates
            limit=5,
        )
    except (GeocoderTimedOut, GeocoderServiceError):
        return None
    
    if not results:
        return None
    
    # Filter to actual populated places (towns/cities), not random POIs
    # then sort by importance descending

    candidates = [r for r in results if isinstance(r, Location)] # type: ignore
    candidates.sort(key=lambda r: r.raw.get("importance", 0), reverse=True)
    
    best = candidates[0]
    return GeocodedLocation(
        query=location,
        display_name=best.address,
        lat=best.latitude,
        lon=best.longitude,
    )
    
def get_timezone(lat: float, lon: float) -> str:
    """Get IANA timezone name for a coordinate.

    Returns 'UTC' as fallback if lookup fails.
    """
    tz = _tz_finder.timezone_at(lat=lat, lng=lon)
    return tz or "UTC"

def _parse_state_county(display_name: str) -> tuple[str | None, str | None]:
    """Extract state and county from Nominatim display name.

    """
    parts = [p.strip() for p in display_name.split(",")]
    # Filter out empty parts, "United States", "USA", and ZIP codes
    cleaned = [
        p for p in parts
        if p
        and "United States" not in p
        and "USA" not in p
        and not p.strip().isdigit()
    ]
    
    state = None
    county = None
    
    for part in cleaned:
        if "County" in part or "Parish" in part or "Borough" in part:
            county = part.replace("County", "").replace("Parish", "").replace("Borough", "").strip().upper()
    
    # State is the last remaining part after filtering
    if cleaned:
        state = cleaned[-1].strip().upper()
    
    return state, county

def _get_event_city(location_display_name: str) -> str | None:
    """Extract just the city name from a Nominatim display name.
    """
    if not location_display_name:
        return None
    return location_display_name.split(",")[0].strip()