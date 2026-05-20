"""Auto-discover VTEC events at a location during a time window.

Uses IEM's vtec_events_bypoint.py endpoint to find all NWS-issued
warnings whose polygons contained a given lat/lon during the window.
"""
from datetime import datetime, timedelta
from typing import Any
from urllib import response

import httpx

from src import config
from src.models import VTECEventRef

BYPOINT_ENDPOINT = f"{config.IEM_API_BASE}/json/vtec_events_bypoint.py"


def discover_events(
    lat: float,
    lon: float,
    start: datetime,
    end: datetime,
    phenomena: tuple[str, ...] = ("TO", "SV"),
    significance: tuple[str, ...] = ("W",),
) -> list[VTECEventRef]:
    """Find VTEC events whose SBW polygon contained (lat, lon) in the time window.

    Args:
        lat, lon: Point in decimal degrees
        start, end: Time window (UTC)
        phenomena: VTEC phenomena codes to include. Defaults to TO (Tornado)
                   and SV (Severe Thunderstorm).
        significance: VTEC significance codes. Defaults to W (Warning).

    Returns a list of VTECEventRef, filtered to the time window and phenomena.
    """
    params = {
    "lat": lat,
    "lon": lon,
    "sdate": start.strftime("%Y-%m-%d"),
    "edate": (end + timedelta(days=1)).strftime("%Y-%m-%d"),
    }
    response = httpx.get(BYPOINT_ENDPOINT, params=params, timeout=30.0)
    print(f"  Request URL: {response.url}")
    print(f"  Response: {response.text[:500]}")
    
    
    response.raise_for_status()
    raw = response.json()

    pheno_set = set(phenomena)
    sig_set = set(significance)

    results: list[VTECEventRef] = []
    for event in raw.get("events", []):
        
        print(f"  Window: {start} to {end}")
        
        print(f"  Checking: {event.get('phenomena')}.{event.get('significance')}.{event.get('eventid')} issued {event.get('issue')}")
        if event.get("phenomena") not in pheno_set:
            print(f"    skipped: phenomena {event.get('phenomena')} not in {pheno_set}")
            continue
        if event.get("significance") not in sig_set:
            print(f"    skipped: significance {event.get('significance')} not in {sig_set}")
            continue

        issue_str = event.get("issue")
        if not issue_str:
            print(f"    skipped: no issue time")
            continue
        issue = datetime.fromisoformat(issue_str.replace("Z", "+00:00"))
        if issue < start or issue > end:
            print(f"    skipped: {issue} outside [{start}, {end}]")
            continue

        # Extract year from the issue time (events span calendar years)
        results.append(VTECEventRef(
            wfo=event["wfo"],
            year=issue.year,
            phenomena=event["phenomena"],
            significance=event["significance"],
            etn=event["eventid"],
        ))

    return results