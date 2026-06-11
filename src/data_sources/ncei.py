"""NCEI Storm Events Database client.

Downloads annual CSV files from:
https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/

Files are cached locally by year to avoid re-downloading.
"""
import csv
import gzip
import io
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from src import config

NCEI_BASE_URL = "https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/"
NCEI_CACHE_DIR = config.CACHE_DIR / "ncei"


_FILENAME_CACHE: dict[int, str] = {}

def _get_csv_filename(year: int) -> str | None:
    if year in _FILENAME_CACHE:
        return _FILENAME_CACHE[year]
    print(f"  NCEI: checking directory for {year} data...")
    try:
        r = httpx.get(NCEI_BASE_URL, timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0))
        r.raise_for_status()
        pattern = rf"StormEvents_details-ftp_v1\.0_d{year}_c\d+\.csv\.gz"
        matches = re.findall(pattern, r.text)
        if not matches:
            print(f"  NCEI: no file found for {year}")
            return None
        filename = sorted(matches)[-1]
        _FILENAME_CACHE[year] = filename
        print(f"  NCEI: found {filename}")
        return filename
    except Exception as e:
        print(f"  NCEI: directory check failed: {e}")
        return None


def _get_cached_path(year: int) -> Path | None:
    existing = sorted(NCEI_CACHE_DIR.glob(f"StormEvents_details-ftp_v1.0_d{year}_c*.csv.gz"))
    return existing[-1] if existing else None


def _download_year(year: int) -> Path | None:
    NCEI_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    cached = _get_cached_path(year)

    current_year = date.today().year
    should_check_update = year >= current_year - 1

    if cached and not should_check_update:
        return cached  # historical years: never check for updates

    # For recent years, check if cached file is recent enough (< 7 days old)
    if cached:
        import os
        age_days = (date.today() - date.fromtimestamp(os.path.getmtime(cached))).days
        if age_days < 7:
            return cached  # cached recently enough, skip directory check

    # Need to check for updates
    filename = _get_csv_filename(year)
    if filename is None:
        return cached

    latest_path = NCEI_CACHE_DIR / filename
    if latest_path.exists():
        return latest_path

    print(f"  NCEI: downloading {filename}...")
    url = NCEI_BASE_URL + filename
    r = httpx.get(url, timeout=120.0, follow_redirects=True)
    r.raise_for_status()
    latest_path.write_bytes(r.content)

    if cached and cached != latest_path:
        cached.unlink()
        print(f"    Replaced {cached.name}")

    print(f"    Cached to {latest_path.name}")
    return latest_path


def _parse_damage(value: str) -> float | None:
    """Parse NCEI damage strings like '10.00B', '500.00K', '1.50M' to dollars."""
    if not value or value.strip() == "0.00":
        return None
    value = value.strip().upper()
    multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    if value[-1] in multipliers:
        try:
            return float(value[:-1]) * multipliers[value[-1]]
        except ValueError:
            return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_ncei_datetime(value: str) -> datetime | None:
    """Parse NCEI date formats."""
    if not value:
        return None
    for fmt in (
        "%d-%b-%y %H:%M:%S",    
        "%d-%b-%Y %H:%M:%S",   
        "%m/%d/%Y %H:%M",      
        "%m/%d/%Y %H:%M:%S",   
    ):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def fetch_storm_events(
    event_date: date,
    state: str,
    county: str | None = None,
    event_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch NCEI Storm Events matching the given date, state, and optionally county.

    Args:
        event_date: The date of the event
        state: State name in uppercase (e.g., 'IOWA', 'OKLAHOMA')
        county: County name in uppercase (e.g., 'ADAIR', 'GARFIELD'). Optional.
        event_types: List of event types to filter (e.g., ['Tornado', 'Hail']).
                     If None, returns all types.

    Returns:
        List of event dicts with standardized fields.
    """
    cache_path = _download_year(event_date.year)
    if cache_path is None:
        print(f"  NCEI: no data file found for {event_date.year}")
        return []

    results = []
    with gzip.open(cache_path, "rt", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Filter by state
            if row.get("STATE", "").upper() != state.upper():
                continue

            # Filter by county if provided
            if county and county.upper() not in row.get("CZ_NAME", "").upper():
                continue

            # Filter by event type if provided
            if event_types:
                if row.get("EVENT_TYPE", "") not in event_types:
                    continue

            # Filter by date - check event date and day before (handles UTC boundary crossings)
            begin_dt = _parse_ncei_datetime(row.get("BEGIN_DATE_TIME", ""))
            if begin_dt is None:
                continue
            if begin_dt.date() not in (event_date, event_date - timedelta(days=1)):
                continue

            results.append({
                "event_type": row.get("EVENT_TYPE"),
                "begin_time": row.get("BEGIN_DATE_TIME"),
                "end_time": row.get("END_DATE_TIME"),
                "state": row.get("STATE"),
                "county": row.get("CZ_NAME"),
                "tor_f_scale": row.get("TOR_F_SCALE"),
                "tor_length_mi": row.get("TOR_LENGTH"),
                "tor_width_yd": row.get("TOR_WIDTH"),
                "episode_id": row.get("EPISODE_ID"),
                "event_id": row.get("EVENT_ID"),
                "deaths_direct": int(row.get("DEATHS_DIRECT") or 0),
                "deaths_indirect": int(row.get("DEATHS_INDIRECT") or 0),
                "injuries_direct": int(row.get("INJURIES_DIRECT") or 0),
                "injuries_indirect": int(row.get("INJURIES_INDIRECT") or 0),
                "damage_property": _parse_damage(row.get("DAMAGE_PROPERTY", "")),
                "damage_crops": _parse_damage(row.get("DAMAGE_CROPS", "")),
                "begin_lat": row.get("BEGIN_LAT"),
                "begin_lon": row.get("BEGIN_LON"),
                "end_lat": row.get("END_LAT"),
                "end_lon": row.get("END_LON"),
                "event_narrative": row.get("EVENT_NARRATIVE"),
                "episode_narrative": row.get("EPISODE_NARRATIVE"),
            })

    return results