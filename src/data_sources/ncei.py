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


def _get_csv_filename(year: int) -> str | None:
    """Find the filename for a given year by scraping the directory listing."""
    r = httpx.get(NCEI_BASE_URL, timeout=30.0)
    r.raise_for_status()
    # Filenames look like: StormEvents_details-ftp_v1.0_d2011_c20230927.csv.gz
    pattern = rf"StormEvents_details-ftp_v1\.0_d{year}_c\d+\.csv\.gz"
    match = re.search(pattern, r.text)
    return match.group(0) if match else None


def _get_cached_path(year: int) -> Path | None:
    """Return cached path if it exists, checking for updates."""
    existing = list(NCEI_CACHE_DIR.glob(f"StormEvents_details-ftp_v1.0_d{year}_c*.csv.gz"))
    if not existing:
        return None
    return existing[0]  # return whatever's cached


def _download_year(year: int) -> Path | None:
    NCEI_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    
    # Check if we have a cached version
    cached = _get_cached_path(year)
    
    # For recent years (current year and last year), always check for updates
    current_year = date.today().year
    should_check_update = year >= current_year - 1
    
    if cached and not should_check_update:
        return cached  # historical years: use cache forever
    
    # Check directory for latest filename
    filename = _get_csv_filename(year)
    if filename is None:
        return cached  # fall back to cache if directory check fails
    
    latest_path = NCEI_CACHE_DIR / filename
    if latest_path.exists():
        return latest_path  # already have the latest
    
    # New file available — download it
    print(f"  Downloading updated NCEI Storm Events for {year}...")
    url = NCEI_BASE_URL + filename
    r = httpx.get(url, timeout=120.0, follow_redirects=True)
    r.raise_for_status()
    latest_path.write_bytes(r.content)
    
    # Delete old cached version if different
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
        "%d-%b-%y %H:%M:%S",    # 21-MAY-24 20:45:00
        "%d-%b-%Y %H:%M:%S",   # 21-MAY-2024 20:45:00
        "%m/%d/%Y %H:%M",      # 6/20/2025 22:04
        "%m/%d/%Y %H:%M:%S",   # 6/20/2025 22:04:00
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