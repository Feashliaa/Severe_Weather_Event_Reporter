"""Iowa Environmental Mesonet archive client.

The IEM API splits warning data across three endpoints:
- /json/vtec_events.py    - metadata (issue/expire times, names) for all events
                            in a given WFO/year
- /geojson/vtec_event.py with sbw=1   - polygon geometry
- /geojson/vtec_event.py with lsrs=1  - associated Local Storm Reports

We fetch all three and combine them into a single warning record.
"""
from typing import Any
from functools import lru_cache
import json

import httpx

from src import config

VTEC_EVENT_ENDPOINT = f"{config.IEM_API_BASE}/geojson/vtec_event.py"
VTEC_EVENTS_LIST_ENDPOINT = f"{config.IEM_API_BASE}/json/vtec_events.py"
LSR_BBOX_ENDPOINT = f"{config.IEM_API_BASE}/geojson/lsr.geojson"


def _get(url: str, params: dict[str, Any]) -> dict[str, Any]:
    """Wrapper around httpx.get with consistent timeout + error handling."""
    response = httpx.get(url, params=params, timeout=30.0)
    response.raise_for_status()
    return response.json()


@lru_cache(maxsize=32)
def _fetch_events_list(wfo: str, year: int) -> str:
    """Fetch all VTEC events for a WFO+year. Returns JSON string (for caching)."""
    raw = _get(VTEC_EVENTS_LIST_ENDPOINT, {"wfo": wfo, "year": year})
    return json.dumps(raw)


def fetch_event_metadata(
    wfo: str,
    year: int,
    phenomena: str,
    significance: str,
    etn: int,
) -> dict[str, Any] | None:
    """Find a single VTEC event's metadata in the year+WFO list."""
    raw = json.loads(_fetch_events_list(wfo, year))
    events = raw.get("events", [])
    for event in events:
        if (
            event.get("phenomena") == phenomena
            and event.get("significance") == significance
            and event.get("eventid") == etn
        ):
            return event
    return None


def fetch_polygon(
    wfo: str,
    year: int,
    phenomena: str,
    significance: str,
    etn: int,
) -> dict[str, Any] | None:
    """Fetch the Storm Based Warning polygon for a VTEC event."""
    raw = _get(
        VTEC_EVENT_ENDPOINT,
        {
            "wfo": wfo,
            "year": year,
            "phenomena": phenomena,
            "significance": significance,
            "etn": etn,
            "sbw": 1,
        },
    )
    features = raw.get("features", [])
    polygon_feature = next(
        (f for f in features if f.get("geometry", {}).get("type") in ("Polygon", "MultiPolygon")),
        None,
    )
    return polygon_feature.get("geometry") if polygon_feature else None


def fetch_lsrs_by_bbox(
    sts: str,
    ets: str,
    west: float,
    east: float,
    south: float,
    north: float,
) -> list[dict[str, Any]]:
    """Fetch all LSRs in a bounding box within a time window.

    Unlike fetch_lsrs() (which is scoped to a single warning polygon),
    this returns every LSR in the region - including ones filed after
    warnings expired or not associated with any specific VTEC event.

    Args:
        sts: Start time as ISO string (e.g., "2026-05-20T19:00Z")
        ets: End time as ISO string
        west, east, south, north: Bounding box in decimal degrees
    """
    params = {
        "sts": sts,
        "ets": ets,
        "west": west,
        "east": east,
        "south": south,
        "north": north,
    }
    raw = _get(LSR_BBOX_ENDPOINT, params)

    lsrs = []
    for f in raw.get("features", []):
        geom = f.get("geometry", {})
        if geom.get("type") != "Point":
            continue
        props = f.get("properties", {})
        coords = geom.get("coordinates", [None, None])
        lsrs.append({
            "time": props.get("utc_valid") or props.get("valid"),
            "lon": coords[0] if coords else None,
            "lat": coords[1] if len(coords) > 1 else None,
            "event": props.get("event") or props.get("typetext"),
            "type_code": props.get("type"),
            "magnitude": props.get("magnitude"),
            "city": props.get("city"),
            "county": props.get("county"),
            "state": props.get("state"),
            "remarks": props.get("remark"),
            "product_id": props.get("product_id"),
        })
    return lsrs


def fetch_event_bundle(
    wfo: str,
    year: int,
    phenomena: str,
    significance: str,
    etn: int,
) -> dict[str, Any]:
    """Fetch a VTEC event with its metadata and polygon."""
    meta = fetch_event_metadata(wfo, year, phenomena, significance, etn)
    if meta is None:
        return {}

    polygon = fetch_polygon(wfo, year, phenomena, significance, etn)

    return {
        "vtec_id": f"{wfo}.{phenomena}.{significance}.{etn:04d}",
        "type": f"{meta.get('ph_name')} {meta.get('sig_name')}",
        "phenomena": meta.get("phenomena"),
        "significance": meta.get("significance"),
        "issued_at": meta.get("issue"),
        "expires_at": meta.get("expire"),
        "wfo": meta.get("wfo"),
        "etn": meta.get("eventid"),
        "locations": meta.get("locations"),
        "forecaster": meta.get("fcster"),
        "polygon": polygon,
        "url": f"https://mesonet.agron.iastate.edu{meta.get('url', '')}",
    }