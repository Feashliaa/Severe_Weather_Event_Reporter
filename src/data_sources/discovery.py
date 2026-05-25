"""Auto-discover VTEC events at a location during a time window.

Uses IEM's vtec_events_bypoint.py endpoint to find all NWS-issued
warnings whose polygons contained a given lat/lon during the window.
"""
from datetime import datetime, timedelta
from src import config
from src.models import VTECEventRef

import httpx


BYPOINT_ENDPOINT = f"{config.IEM_API_BASE}/json/vtec_events_bypoint.py"


def discover_events(
    lat: float,
    lon: float,
    start: datetime,
    end: datetime,
    phenomena: tuple[str, ...] = ("TO", "SV"),
    significance: tuple[str, ...] = ("W",),
) -> list[VTECEventRef]:
    # Search center + 8 surrounding points in a 15km grid
    # This handles cases where the user's location is near but not inside
    # the warning polygon
    offsets = [
        (0, 0),
        (0.15, 0), (-0.15, 0), (0, 0.15), (0, -0.15),
        (0.15, 0.15), (0.15, -0.15), (-0.15, 0.15), (-0.15, -0.15),
    ]
    
    seen_etns: set[tuple] = set()
    results: list[VTECEventRef] = []
    
    for dlat, dlon in offsets:
        params = {
            "lat": lat + dlat,
            "lon": lon + dlon,
            "sdate": (start - timedelta(days=1)).strftime("%Y-%m-%d"),
            "edate": (end + timedelta(days=1)).strftime("%Y-%m-%d"),
        }
        response = httpx.get(BYPOINT_ENDPOINT, params=params, timeout=30.0)
        response.raise_for_status()
        raw = response.json()

        pheno_set = set(phenomena)
        sig_set = set(significance)

        for event in raw.get("events", []):
            if event.get("phenomena") not in pheno_set:
                continue
            if event.get("significance") not in sig_set:
                continue
            issue_str = event.get("issue")
            if not issue_str:
                continue
            issue = datetime.fromisoformat(issue_str.replace("Z", "+00:00"))
            print(f"    Checking {event.get('phenomena')}.{event.get('significance')}.{event.get('eventid')} issued {issue} vs window [{start}, {end}]")
            if issue < start or issue > end:
                print(f"      SKIPPED: outside window")
                continue
            key = (event["wfo"], event["phenomena"], event["significance"], event["eventid"])
            if key not in seen_etns:
                seen_etns.add(key)
                results.append(VTECEventRef(
                    wfo=event["wfo"],
                    year=issue.year,
                    phenomena=event["phenomena"],
                    significance=event["significance"],
                    etn=event["eventid"],
                ))

    return results