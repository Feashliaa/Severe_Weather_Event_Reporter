"""NWS Damage Assessment Toolkit (DAT) client.

Queries the DAT ArcGIS API for tornado track geometry.
Coverage: ~2000-present. Falls back to NCEI when no data available.

Layer 0: Damage Points
Layer 1: Damage Lines (curved multi-pointed track)
Layer 2: Damage Polygons (variable-width damage corridor)
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from src import config

DAT_BASE = "https://services.dat.noaa.gov/arcgis/rest/services/nws_damageassessmenttoolkit/DamageViewer/FeatureServer"
DAT_CACHE_DIR = config.CACHE_DIR / "dat"

def _timestamp_window(event_date: datetime) -> tuple[str, str]:
    """Return ArcGIS time stamp strings for a +/- 2 day window"""
    start = (event_date - timedelta(days=1)).strftime("%Y-%m-%d 00:00:00")
    end = (event_date + timedelta(days=2)).strftime("%Y-%m-%d 00:00:00")
    return start, end

def _parse_ef(raw: str) -> str:
    """Normalize EF scale from raw string."""
    if not raw:
        return 'EFU'
    raw = raw.strip()
    if raw.startswith('EFEF'): # remove duplicate EF prefix
        return raw[2:]
    return raw

def _query_layer(layer: int, bbox: tuple, where: str, cache_key: str) -> list[dict]:
    DAT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = DAT_CACHE_DIR / f"layer{layer}_{cache_key}.json"
    
    if cache_path.exists():
        print(f"  DAT: loading from cache {cache_path}")
        data = json.loads(cache_path.read_text())
    else:
        min_lon, min_lat, max_lon, max_lat = bbox
        geom_str = f'{min_lon},{min_lat},{max_lon},{max_lat}'
        print(f"  DAT layer {layer}: geometry={geom_str} where={where}")
        try:
            r = httpx.get(f"{DAT_BASE}/{layer}/query", params={
                'geometry': geom_str,
                'geometryType': 'esriGeometryEnvelope',
                'inSR': '4326',
                'spatialRel': 'esriSpatialRelIntersects',
                'where': where,
                'outFields': '*',
                'returnGeometry': 'true',
                'f': 'json'
            }, timeout=30)
            r.raise_for_status()
            data = r.json()
            print(f"  DAT layer {layer}: {len(data.get('features', []))} features, error={data.get('error')}")
            cache_path.write_text(r.text)
        except Exception as e:
            print(f"  DAT layer {layer} failed: {e}")
            return []
    return data.get('features', [])

def fetch_tornado_tracks(
    bbox: tuple[float, float, float, float],
    event_date: datetime
) -> dict [str, Any]:
    """
    Fetch DAT tornado geometry for an event.
    
    Return dict with:
        'polygons': list of polygon track dicts (layer 2) - variable-width damage corridors
        'lines': list of line track dicts (layer 1) - curved multi-pointed centerlines
        
    Both lists may be empty if the DAT has no data for the event.
    bbox: (min_lon, min_lat, max_lon, max_lat)
    """
    start_ts, end_ts = _timestamp_window(event_date)
    where = f"stormdate >= timestamp '{start_ts}' AND stormdate <= timestamp '{end_ts}'"
    
    # Use a cache key based on the bbox and event date to avoid redundant queries
    # Set precision to 2 decimal places to allow for some spatial tolerance in caching
    cache_key = f"{bbox[0]:.2f}_{bbox[1]:.2f}_{bbox[2]:.2f}_{bbox[3]:.2f}_{event_date.strftime('%Y%m%d')}"
    
    # layer 2 - polygons
    polygons = []
    for feat in _query_layer(2, bbox, where, cache_key):
        attrs = feat.get('attributes', {})
        rings = feat.get('geometry', {}).get('rings', [[]])
        if not rings or not rings[0]:
            continue
        # convert the [lon, lat] pairs to [lat, lon] for consistency with other data sources
        coords = [[pt[1], pt[0]] for pt in rings[0]]
        polygons.append({
            'ef_scale': _parse_ef(attrs.get('efscale', '')),
            'ef_num': attrs.get('efnum', -1),
            'length_mi': attrs.get('length'),
            'width_yd': attrs.get('width'),
            'fatalities': attrs.get('fatalities', 0),
            'injuries': attrs.get('injuries', 0),
            'event_id': attrs.get('event_id'),
            'comments': attrs.get('comments'),
            'coords': coords,
        })
    
    # layer 1 - lines
    lines = []
    for feat in _query_layer(1, bbox, where, cache_key):
        attrs = feat.get('attributes', {})
        paths = feat.get('geometry', {}).get('paths', [[]])
        
        if not paths or not paths[0]:
            continue
        coords = [[pt[1], pt[0]] for pt in paths[0]]
        lines.append({
            'ef_scale': _parse_ef(attrs.get('efscale', '')),
            'ef_num': attrs.get('efnum', -1),
            'length_mi': attrs.get('length'),
            'width_yd': attrs.get('width'),
            'fatalities': attrs.get('fatalities', 0),
            'injuries': attrs.get('injuries', 0),
            'max_wind': attrs.get('maxwind'),
            'event_id': attrs.get('event_id'),
            'wfo': attrs.get('wfo'),
            'comments': attrs.get('comments'),
            'coords': coords,
        })
        
    return {
        'polygons': polygons,
        'lines': lines
    }
            