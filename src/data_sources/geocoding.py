"""Geocode location names to coordinates."""
from dataclasses import dataclass
from functools import lru_cache

from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

from timezonefinder import TimezoneFinder

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


def geocode(location: str) -> GeocodedLocation | None:
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
    from geopy.location import Location
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