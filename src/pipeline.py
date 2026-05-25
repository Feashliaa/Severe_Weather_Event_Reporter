"""End-to-end pipeline: event params in, structured report data out.

Orchestrates data fetching, radar processing, and LLM narrative generation.
Each step is extracted into a helper function for testability and clarity.
"""
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.ops import unary_union

from src import config
from src.data_sources import discovery, geocoding, iem, nexrad, radar_locator
from src.feature_gates import feature_availability, FeatureAvailability
from src.models import VTECEventRef
from src.radar import processor as radar_processor
from src.radar import renderer as radar_renderer
from src.report.builder import EventReport, generate_narrative

from concurrent.futures import ThreadPoolExecutor, as_completed

from typing import Callable


# ---------------------------------------------------------------------------
# Step helpers
# ---------------------------------------------------------------------------

def _geocode_location(
    location: str,
    zoom_lat: float | None,
    zoom_lon: float | None,
) -> tuple[float, float]:
    """Return (lat, lon) — geocodes if not already provided."""
    if zoom_lat is not None and zoom_lon is not None:
        return zoom_lat, zoom_lon
    print(f"  Geocoding location: {location}...")
    geo = geocoding.geocode(location)
    if geo is None:
        raise ValueError(f"Could not geocode location: {location}")
    print(f"    => {geo.lat:.3f}, {geo.lon:.3f} ({geo.display_name})")
    return geo.lat, geo.lon


def _select_radar(
    zoom_lat: float,
    zoom_lon: float,
    start: datetime,
    end: datetime,
    radar_site: str | None,
    progress: Callable[[str], None] | None = None,
) -> tuple[str, str | None, float | None, float | None]:
    """Select a radar site and return (icao, commissioned, site_lat, site_lon).

    If radar_site is None, finds the nearest site that has S3 data for the
    given time window. Falls back to progressively farther sites if the
    nearest has no archive data.
    """
    
    def _p(msg: str) -> None:
        print(msg)
        if progress: progress(msg)
    
    if radar_site is None:
        print(f"  Finding nearest NEXRAD to ({zoom_lat:.3f}, {zoom_lon:.3f})...")
        candidates = radar_locator.find_radars_within(zoom_lat, zoom_lon, radius_km=230.0)

        for candidate in candidates:
            test_scans = nexrad.list_scans(candidate.icao, start, end)
            if test_scans:
                _p(f"    => {candidate.icao}: {candidate.name} ({candidate.distance_km}km) - {len(test_scans)} scans found")
                station = radar_locator.get_station(candidate.icao)
                return (
                    candidate.icao,
                    candidate.commissioned,
                    station.lat if station else None,
                    station.lon if station else None,
                )
            else:
                _p(f"    Skipping {candidate.icao} ({candidate.name}, {candidate.distance_km}km) - no scans on S3")

        raise ValueError(
            f"No NEXRAD data found within 230km of ({zoom_lat:.3f}, {zoom_lon:.3f}) for {start.date()}"
        )
    else:
        # User-specified radar — look up its metadata
        commissioned = None
        for s in radar_locator._load_stations():
            if s["icao"] == radar_site:
                commissioned = s.get("commissioned")
                break
        station = radar_locator.get_station(radar_site)
        return (
            radar_site,
            commissioned,
            station.lat if station else None,
            station.lon if station else None,
        )


def _fetch_warnings(
    vtec_events: list[VTECEventRef] | None,
    auto_discover: bool,
    avail: FeatureAvailability,
    zoom_lat: float,
    zoom_lon: float,
    start: datetime,
    end: datetime,
    phenomena: tuple[str, ...],
    progress: Callable[[str], None] | None = None,
) -> tuple[list, list[VTECEventRef]]:
    """Fetch warning metadata. Returns (warnings, vtec_events_used)."""
    
    def _p(msg: str) -> None:
        print(msg)
        if progress: progress(msg)
    
    if auto_discover and not vtec_events:
        if avail.vtec_warnings:
            _p(f"  Discovering warnings at ({zoom_lat:.3f}, {zoom_lon:.3f})...")
            vtec_events = discovery.discover_events(
                lat=zoom_lat,
                lon=zoom_lon,
                start=start,
                end=end,
                phenomena=phenomena,
            )
            if not avail.sbw_polygons:
                _p("  Note: pre-2007 warnings lack polygon geometry; LSR polygon filtering skipped")
            _p(f"    => Found {len(vtec_events)} matching events")
        else:
            _p("  Skipping warning auto-discovery (predates VTEC archive)")
            vtec_events = []

    warnings = []
    for ref in (vtec_events or []):
        _p(f"  Fetching {ref.wfo}.{ref.phenomena}.{ref.significance}.{ref.etn}...")
        warning = iem.fetch_event_bundle(
            ref.wfo, ref.year, ref.phenomena, ref.significance, ref.etn
        )
        if warning:
            warnings.append(warning)

    return warnings, vtec_events or []


def _fetch_lsrs(
    avail: FeatureAvailability,
    zoom_lat: float,
    zoom_lon: float,
    lsr_search_km: float,
    start: datetime,
    end: datetime,
    warnings: list,
    progress: Callable[[str], None] | None = None,
) -> list:
    """Fetch LSRs by bounding box, deduplicate, and filter to warning polygons."""
    
    def _p(msg: str) -> None:
        print(msg)
        if progress: progress(msg)
    
    if not avail.iem_lsr:
        _p("  Skipping LSR fetch (data not reliably archived for this era)")
        return []

    deg_per_km_lat = 1 / 111.0
    deg_per_km_lon = 1 / (111.0 * abs(math.cos(math.radians(zoom_lat))))
    lat_pad = lsr_search_km * deg_per_km_lat
    lon_pad = lsr_search_km * deg_per_km_lon

    lsr_start = start - timedelta(hours=1)
    lsr_end = end + timedelta(hours=6)

    _p(f"  Fetching LSRs within {lsr_search_km:.0f}km of {zoom_lat:.3f}, {zoom_lon:.3f}...")
    bbox_lsrs = iem.fetch_lsrs_by_bbox(
        sts=lsr_start.strftime("%Y-%m-%dT%H:%MZ"),
        ets=lsr_end.strftime("%Y-%m-%dT%H:%MZ"),
        west=zoom_lon - lon_pad,
        east=zoom_lon + lon_pad,
        south=zoom_lat - lat_pad,
        north=zoom_lat + lat_pad,
    )
    _p(f"    Got {len(bbox_lsrs)} LSRs")

    # Deduplicate
    seen: set[tuple] = set()
    lsrs = []
    for lsr in bbox_lsrs:
        key = (lsr.get("time"), lsr.get("lat"), lsr.get("lon"), lsr.get("event"))
        if key not in seen:
            seen.add(key)
            lsrs.append(lsr)

    # Filter to warning polygons if SBW data is available
    if warnings and lsrs and avail.sbw_polygons:
        polys = []
        for w in warnings:
            if w.get("polygon"):
                try:
                    polys.append(shape(w["polygon"]))
                except Exception:
                    pass
        if polys:
            coverage = unary_union(polys)
            before = len(lsrs)
            lsrs = [
                lsr for lsr in lsrs
                if lsr.get("lat") and lsr.get("lon")
                and coverage.contains(Point(lsr["lon"], lsr["lat"]))
            ]
            _p(f"    Polygon filter: {before} => {len(lsrs)} LSRs")

    return lsrs


def _process_scan(
    path: Path,
    images_dir: Path,
    zoom_lat: float,
    zoom_lon: float,
    zoom_km: float,
    radar_site_lat: float | None,
    radar_site_lon: float | None,
    progress: Callable[[str], None] | None = None,
) -> tuple[dict, str] | None:
    """Process a single radar scan. Returns (features_dict, image_path) or None on failure"""
    
    def _p(msg: str) -> None:
        print(msg)
        if progress: progress(msg)
    
    try:
        features = radar_processor.extract_features(path)
        ts_safe = features.timestamp.replace(":", "-").replace(".", "-")[:19]
        img_path = images_dir / f"{ts_safe}_reflectivity.png"
        radar_renderer.render_radar_panel(
            path,
            img_path,
            center_lat=zoom_lat,
            center_lon=zoom_lon,
            zoom_km=zoom_km,
            radar_site_lat=radar_site_lat,
            radar_site_lon=radar_site_lon,
        )
        _p(f"    {features.timestamp}: {features.max_reflectivity_dbz} dBZ, top {features.echo_top_18dbz_kft} kft => rendered {img_path.name}")
        return features.to_dict(), f"images/{img_path.name}"
    except Exception as e:
        _p(f"    Failed to process {path.name}: {e}")
        return None


def _process_radar(
    radar_site: str,
    start: datetime,
    end: datetime,
    max_radar_scans: int,
    images_dir: Path,
    zoom_lat: float,
    zoom_lon: float,
    zoom_km: float,
    radar_site_lat: float | None,
    radar_site_lon: float | None,
    progress: Callable[[str], None] | None = None,
) -> tuple[list, list]:
    """Download and process radar scans. Returns (radar_features, radar_images)."""
    
    def _p(msg: str) -> None:
        print(msg)
        if progress: progress(msg)
    
    _p(f"  Listing radar scans for {radar_site} between {start} and {end}...")
    all_scans = nexrad.list_scans(radar_site, start, end)
    _p(f"    Found {len(all_scans)} scans, selecting up to {max_radar_scans}...")
    key_scans = nexrad.pick_key_scans(all_scans, max_scans=max_radar_scans)

    _p(f"  Downloading {len(key_scans)} scans...")
    local_paths = nexrad.download_scans(key_scans)

    _p(f"  Processing {len(local_paths)} scans (parallel) ...")

    # Currently 2 threads
    n_workers = min(2, len(local_paths))

    results: dict[Path, tuple[dict, str] | None] = {}
    completed = 0
    with ThreadPoolExecutor(max_workers=n_workers) as executor:
        futures = {
            executor.submit(
                _process_scan,
                path, images_dir, zoom_lat, zoom_lon, zoom_km,
                radar_site_lat, radar_site_lon, progress,
            ): path
            for path in local_paths
        }
        for future in as_completed(futures):
            path = futures[future]
            results[path] = future.result()
            completed += 1
            _p(f"  Rendered scan {completed} of {len(local_paths)}...")

    # Reassemble in original scan order (as_completed returns out of order)
    radar_features = []
    radar_images = []
    for path in local_paths:
        result = results.get(path)
        if result is not None:
            features_dict, img_path = result
            radar_features.append(features_dict)
            radar_images.append(img_path)

    return radar_features, radar_images


def _write_outputs(
    event_output_dir: Path,
    slug: str,
    event_name: str,
    location: str,
    event_date: str,
    radar_site: str,
    report: EventReport,
    local_timezone: str,
) -> None:
    """Write manifest.json and report_data.json to the event output directory."""
    manifest = {
        "slug": slug,
        "event_name": event_name,
        "location": location,
        "event_date": event_date,
        "radar_site": radar_site,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (event_output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    report_data = {
        "event_name": report.event_name,
        "event_date": report.event_date,
        "location": location,
        "radar_site": radar_site,
        "local_timezone": local_timezone,
        "narrative": report.narrative,
        "warnings": report.warnings,
        "lsrs": report.lsrs,
        "radar_features": report.radar_features,
        "radar_images": report.radar_images,
        "feature_notes": report.feature_notes,
    }
    (event_output_dir / "report_data.json").write_text(
        json.dumps(report_data, indent=2, default=str)
    )


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def run_pipeline(
    event_name: str,
    event_date: str,
    location: str,
    radar_site: str | None,
    start: datetime,
    end: datetime,
    vtec_events: list[VTECEventRef] | None = None,
    max_radar_scans: int = 8,
    zoom_lat: float | None = None,
    zoom_lon: float | None = None,
    zoom_km: float = 50.0, # hard to gauge whats best, somewhere between 35km - 75km
    lsr_search_km: float = 75.0,
    local_timezone: str = "UTC",
    auto_discover: bool = False,
    discover_phenomena: tuple[str, ...] = ("TO", "SV"),
    force: bool = False,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Run the full pipeline for one event.

    Returns the path to the event output directory containing
    manifest.json and report_data.json.
    """
    
    def _progress(msg: str) -> None:
        print(msg)
        if progress:
            progress(msg)
    
    slug = event_name.lower().replace(" ", "_")
    event_output_dir = config.OUTPUT_DIR / slug

    if (event_output_dir / "report_data.json").exists() and not force:
        print(f"  Report already exists. Use force=True to regenerate.")
        return event_output_dir

    event_output_dir.mkdir(parents=True, exist_ok=True)
    (event_output_dir / "images").mkdir(exist_ok=True)
    images_dir = event_output_dir / "images"

    # Step 0: resolve location coordinates
    zoom_lat, zoom_lon = _geocode_location(location, zoom_lat, zoom_lon)

    # Auto-scale scan count based on window length
    if max_radar_scans == 8:
        window_hours = (end - start).total_seconds() / 3600
        max_radar_scans = max(8, min(16, int(window_hours * 5)))
        if max_radar_scans > 8:
            print(f"  Auto-scaled to {max_radar_scans} scans for {window_hours:.1f}h window")

    # Step 0a: select radar site
    _progress(f"Finding nearest NEXRAD to ({zoom_lat:.3f}, {zoom_lon:.3f})...")
    radar_site, radar_commissioned, radar_site_lat, radar_site_lon = _select_radar(
        zoom_lat, zoom_lon, start, end, radar_site, progress=progress
    )

    # Step 0b: gate features by date + radar
    _progress(f"  Checking feature availability for {start.date()}...")
    avail = feature_availability(start.date(), radar_commissioned)
    if avail.notes:
        for note in avail.notes:
            print(f"    {note}")

    # Step 0c: fetch warnings
    warnings, vtec_events = _fetch_warnings(
        vtec_events, auto_discover, avail,
        zoom_lat, zoom_lon, start, end, discover_phenomena,
        progress=progress,
    )

    # Step 1: fetch LSRs
    lsrs = _fetch_lsrs(avail, zoom_lat, zoom_lon, lsr_search_km, start, end, warnings)

    # Step 2: process radar
    if avail.radar:
        radar_features, radar_images = _process_radar(
            radar_site, start, end, max_radar_scans,
            images_dir, zoom_lat, zoom_lon, zoom_km,
            radar_site_lat, radar_site_lon,
            progress=progress,
        )
    else:
        print("  Skipping radar (no coverage for this event)")
        radar_features, radar_images = [], []

    # Step 3: assemble report + generate narrative
    report = EventReport(
        event_name=event_name,
        event_date=event_date,
        location=location,
        warnings=warnings,
        lsrs=lsrs,
        radar_features=radar_features,
        radar_images=radar_images,
        feature_notes=avail.notes,
    )

    _progress("Generating narrative...")
    report.narrative = generate_narrative(report)

    # Step 4: write outputs
    _write_outputs(event_output_dir, slug, event_name, location, event_date, radar_site, report, local_timezone)

    return event_output_dir