"""Find the nearest NEXRAD radar to a given location."""
import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


# load file from nexrad_stations.json, which is a static dump, its in the same folder as this script
STATIONS_PATH = Path(__file__).parent / "nexrad_stations.json"


EXCLUDED_ICAOS = {"KCRI", "KOUN"} 

@dataclass
class NexradStation:
    icao: str
    name: str
    state: str
    lat: float
    lon: float
    commissioned: str | None = None
    distance_km: float | None = None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two points."""
    R = 6371.0
    lat1r, lat2r = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1r) * math.cos(lat2r) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


@lru_cache(maxsize=1)
def _load_stations() -> list[dict]:
    with open(STATIONS_PATH) as f:
        return json.load(f)


def find_nearest_radar(lat: float, lon: float, max_distance_km: float = 230.0) -> NexradStation | None:
    """Return the nearest NEXRAD site to (lat, lon).

    Args:
        lat, lon: Point in decimal degrees
        max_distance_km: Reject results farther than this (default 230km,
                         roughly the maximum useful WSR-88D range)
    """
    stations = _load_stations()
    closest: tuple[float, dict] | None = None

    for s in stations:
        if s["icao"] in EXCLUDED_ICAOS:
            continue
        d = _haversine_km(lat, lon, s["lat"], s["lon"])
        if d <= max_distance_km and (closest is None or d < closest[0]):
            closest = (d, s)

    if closest is None:
        return None

    d, s = closest
    return NexradStation(
        icao=s["icao"],
        name=s["name"],
        state=s["state"],
        lat=s["lat"],
        lon=s["lon"],
        commissioned=s.get("commissioned"),
        distance_km=round(d, 1),
    )
    
    
def get_station(icao: str) -> NexradStation | None:
    """Get a NEXRAD station by ICAO code."""
    for s in _load_stations():
        if s["icao"] == icao:
            return NexradStation(
                icao=s["icao"],
                name=s["name"],
                state=s["state"],
                lat=s["lat"],
                lon=s["lon"],
                commissioned=s.get("commissioned"),
            )
    return None



if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python -m src.data_sources.radar_locator <lat> <lon>")
        sys.exit(1)
    nearest = find_nearest_radar(float(sys.argv[1]), float(sys.argv[2]))
    print(nearest)