"""NEXRAD Level II from NOAA's public AWS S3 bucket.

Bucket: noaa-nexrad-level2
Path format: {year}/{month:02d}/{day:02d}/{site}/{site}YYYYMMDD_HHMMSS_V06

Uses the `nexradaws` package which wraps S3 access. No AWS credentials needed.
"""
from datetime import datetime
from pathlib import Path
from typing import Any

import nexradaws

from src import config


_conn = nexradaws.NexradAwsInterface()


def list_scans(site: str, start: datetime, end: datetime) -> list[Any]:
    """List available Level II scans for a radar site in a time window.

    Returns nexradaws AwsNexradFile objects, which have attributes:
        - filename
        - scan_time (datetime)
        - radar_id
        - awspath, key
    """
    scans = _conn.get_avail_scans_in_range(start, end, site)
    # Filter out MDM (metadata-only) files — they're not actual volume scans
    return [s for s in scans if not s.filename.endswith("_MDM")]


def download_scans(
    scans: list[Any],
    dest_dir: Path | None = None,
) -> list[Path]:
    """Download a list of scans to local cache. Returns local file paths."""
    dest_dir = dest_dir or (config.CACHE_DIR / "nexrad")
    dest_dir.mkdir(parents=True, exist_ok=True)

    results = _conn.download(scans, str(dest_dir))
    return [Path(f.filepath) for f in results.success]


def pick_key_scans(
    scans: list[Any],
    max_scans: int = 5,
) -> list[Any]:
    """Down-select a long list of scans to a manageable subset for the report.
    """
    if len(scans) <= max_scans:
        return scans

    step = len(scans) / max_scans
    indices = [int(i * step) for i in range(max_scans)]
    return [scans[i] for i in indices]