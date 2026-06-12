"""End-to-end pipeline: event params in, structured report data out.

Orchestrates data fetching, radar processing, and LLM narrative generation.
Each step is extracted into a helper function for testability and clarity.
"""

import json, re, math, time, os, psutil
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.ops import unary_union

from src import config
from src.data_sources import (
    discovery,
    geocoding,
    iem,
    nexrad,
    radar_locator,
    ncei,
    billion_dollar,
    sounding,
    dat,
    spc_outlook,
)
from src.feature_gates import feature_availability, FeatureAvailability
from src.models import VTECEventRef
from src.radar import processor as radar_processor
from src.radar import renderer as radar_renderer
from src.report.builder import EventReport, generate_narrative, compute_lead_time
from concurrent.futures import ProcessPoolExecutor, as_completed


from typing import Callable

# ---------------------------------------------------------------------------
# Step helpers
# ---------------------------------------------------------------------------

STATE_ABBR = {
    "AL": "ALABAMA",
    "AK": "ALASKA",
    "AZ": "ARIZONA",
    "AR": "ARKANSAS",
    "CA": "CALIFORNIA",
    "CO": "COLORADO",
    "CT": "CONNECTICUT",
    "DE": "DELAWARE",
    "FL": "FLORIDA",
    "GA": "GEORGIA",
    "HI": "HAWAII",
    "ID": "IDAHO",
    "IL": "ILLINOIS",
    "IN": "INDIANA",
    "IA": "IOWA",
    "KS": "KANSAS",
    "KY": "KENTUCKY",
    "LA": "LOUISIANA",
    "ME": "MAINE",
    "MD": "MARYLAND",
    "MA": "MASSACHUSETTS",
    "MI": "MICHIGAN",
    "MN": "MINNESOTA",
    "MS": "MISSISSIPPI",
    "MO": "MISSOURI",
    "MT": "MONTANA",
    "NE": "NEBRASKA",
    "NV": "NEVADA",
    "NH": "NEW HAMPSHIRE",
    "NJ": "NEW JERSEY",
    "NM": "NEW MEXICO",
    "NY": "NEW YORK",
    "NC": "NORTH CAROLINA",
    "ND": "NORTH DAKOTA",
    "OH": "OHIO",
    "OK": "OKLAHOMA",
    "OR": "OREGON",
    "PA": "PENNSYLVANIA",
    "RI": "RHODE ISLAND",
    "SC": "SOUTH CAROLINA",
    "SD": "SOUTH DAKOTA",
    "TN": "TENNESSEE",
    "TX": "TEXAS",
    "UT": "UTAH",
    "VT": "VERMONT",
    "VA": "VIRGINIA",
    "WA": "WASHINGTON",
    "WV": "WEST VIRGINIA",
    "WI": "WISCONSIN",
    "WY": "WYOMING",
}


def _geocode_location(
    location: str,
    zoom_lat: float | None,
    zoom_lon: float | None,
) -> tuple[float, float]:
    """Return (lat, lon) - geocodes if not already provided."""
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
        if progress:
            progress(msg)

    if radar_site is None:
        print(f"  Finding nearest NEXRAD to ({zoom_lat:.3f}, {zoom_lon:.3f})...")
        candidates = radar_locator.find_radars_within(
            zoom_lat, zoom_lon, radius_km=230.0
        )

        for candidate in candidates:
            test_scans = nexrad.list_scans(candidate.icao, start, end)
            if test_scans:
                _p(
                    f"    => {candidate.icao}: {candidate.name} ({candidate.distance_km}km) - {len(test_scans)} scans found"
                )
                station = radar_locator.get_station(candidate.icao)
                return (
                    candidate.icao,
                    candidate.commissioned,
                    station.lat if station else None,
                    station.lon if station else None,
                )
            else:
                _p(
                    f"    Skipping {candidate.icao} ({candidate.name}, {candidate.distance_km}km) - no scans on S3"
                )

        raise ValueError(
            f"No NEXRAD data found within 230km of ({zoom_lat:.3f}, {zoom_lon:.3f}) for {start.date()}"
        )
    else:
        # User-specified radar - look up its metadata
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
        if progress:
            progress(msg)

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
                _p(
                    "  Note: pre-2007 warnings lack polygon geometry; LSR polygon filtering skipped"
                )
            _p(f"    => Found {len(vtec_events)} matching events")
        else:
            _p("  Skipping warning auto-discovery (predates VTEC archive)")
            vtec_events = []

    warnings = []
    for ref in vtec_events or []:
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
        if progress:
            progress(msg)

    if not avail.iem_lsr:
        _p("  Skipping LSR fetch (data not reliably archived for this era)")
        return []

    deg_per_km_lat = 1 / 111.0
    deg_per_km_lon = 1 / (111.0 * abs(math.cos(math.radians(zoom_lat))))
    lat_pad = lsr_search_km * deg_per_km_lat
    lon_pad = lsr_search_km * deg_per_km_lon

    lsr_start = start - timedelta(hours=1)
    lsr_end = end + timedelta(days=3)

    _p(
        f"  Fetching LSRs within {lsr_search_km:.0f}km of {zoom_lat:.3f}, {zoom_lon:.3f}..."
    )
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
                lsr
                for lsr in lsrs
                if lsr.get("lat")
                and lsr.get("lon")
                and coverage.contains(Point(lsr["lon"], lsr["lat"]))
            ]
            _p(f"    Polygon filter: {before} => {len(lsrs)} LSRs")

    return lsrs


def _extract_warning_counties(warnings: list) -> list[tuple[str, str]]:
    """Extract unique (state, county) pairs from warning location strings."""
    results = []
    seen = set()
    for w in warnings:
        locations = w.get("locations", "")
        for match in re.finditer(r"([\w][\w\s]+?)\s*\[([A-Z]{2})\]", locations):
            county = match.group(1).strip().upper()
            abbr = match.group(2)
            state = STATE_ABBR.get(abbr)
            if state and (state, county) not in seen:
                seen.add((state, county))
                results.append((state, county))
    return results


def _fetch_ncei(
    event_date: date,
    location_display_name: str,
    warnings: list | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[dict]:
    """Fetch NCEI Storm Events for the event date and location."""

    def _p(msg: str) -> None:
        print(msg)
        if progress:
            progress(msg)

    # Build list of (state, county) pairs to query
    query_pairs: list[tuple[str, str]] = []

    # Add counties from warning locations
    if warnings:
        warning_counties = _extract_warning_counties(warnings)
        query_pairs.extend(warning_counties)

    # Fall back to geocoded location if no warning counties
    if not query_pairs:
        state, county = geocoding._parse_state_county(location_display_name)
        if not state:
            _p("  Skipping NCEI lookup (could not determine state from location)")
            return []
        query_pairs.append((state, county))  # type: ignore

    _p(
        f"  Fetching NCEI storm events for {len(query_pairs)} counties on {event_date}..."
    )

    all_events = []
    seen_keys: set[tuple] = set()

    for state, county in query_pairs:
        try:
            events = ncei.fetch_storm_events(
                event_date=event_date,
                state=state,
                county=county,
            )
            for e in events:
                key = (
                    e.get("begin_time"),
                    e.get("state"),
                    e.get("county"),
                    e.get("event_type"),
                )
                if key not in seen_keys:
                    seen_keys.add(key)
                    all_events.append(e)
        except Exception as e:
            _p(f"  NCEI fetch failed for {state}/{county}: {e}")

    _p(f"    Found {len(all_events)} NCEI storm events total")
    return all_events


def _filter_ncei_by_warnings(ncei_events: list, warnings: list) -> list:
    """Filter NCEI events to those who's coordinates actually fall within the warning polygons
    Has fallbacks if warnings were too late.
    """
    if not warnings:
        return ncei_events  # let the llm figure it out the best it can

    polygons = []
    for w in warnings:
        if w.get("polygon"):
            try:
                polygons.append(shape(w["polygon"]))
            except Exception:
                pass

    if not polygons:
        return ncei_events  # warnings exist but no polygons

    filtered = []
    unmatched = []

    for e in ncei_events:
        blat = e.get("begin_lat")
        blon = e.get("begin_lon")
        if not blat or not blon:
            filtered.append(e)
            continue
        try:
            pt = Point(float(blon), float(blat))
            if any(poly.contains(pt) for poly in polygons):
                filtered.append(e)
            else:
                unmatched.append(e)
        except (ValueError, TypeError):
            filtered.append(e)

    if not filtered:
        return ncei_events  # filter removed everything, return original

    for e in unmatched:
        scale = e.get("tor_f_scale", "")
        ef_rank = {"EF3": 3, "EF4": 4, "EF5": 5, "F3": 3, "F4": 4, "F5": 5}
        if ef_rank.get(scale, 0) >= 3:
            filtered.append(e)

    return filtered


def _process_scan(
    scan_index: int,
    stagger_slots: int,
    path: Path,
    images_dir: Path,
    zoom_lat: float,
    zoom_lon: float,
    zoom_km: float,
    radar_site_lat: float | None,
    radar_site_lon: float | None,
    event_name: str | None,
) -> tuple[dict, str] | None:
    """Process a single radar scan. Runs in a worker process."""
    time.sleep((scan_index % stagger_slots) * 2.0)
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
            event_label=event_name,
        )
        print(
            f"    {features.timestamp}: {features.max_reflectivity_dbz} dBZ, "
            f"top {features.echo_top_18dbz_kft} kft => rendered {img_path.name}"
        )
        return features.to_dict(), f"images/{img_path.name}"
    except Exception as e:
        print(f"    Failed to process {path.name}: {e}")
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
    event_name: str,
    progress: Callable[[str], None] | None = None,
) -> tuple[list, list, dict]:
    """Download and process radar scans. Returns (radar_features, radar_images)."""

    def _p(msg: str) -> None:
        print(msg)
        if progress:
            progress(msg)

    all_scans = nexrad.list_scans(radar_site, start, end)
    key_scans = nexrad.pick_key_scans(all_scans, max_scans=max_radar_scans)
    t0 = time.time()
    _p(f"  Downloading {len(key_scans)} scans...")
    local_paths = nexrad.download_scans(key_scans)  # local paths
    print(f"    Download took {time.time() - t0:.1f}s")

    t1 = time.time()
    max_workers_env = int(os.environ.get("RADAR_MAX_WORKERS", 4))
    print(
        f"  RADAR_MAX_WORKERS env: {os.environ.get('RADAR_MAX_WORKERS')} -> using {max_workers_env} workers"
    )
    workers = min(len(local_paths), max_workers_env)
    results: dict[int, tuple[dict, str]] = {}

    with ProcessPoolExecutor(max_workers=workers) as ex:

        vad_futures = (
            ex.submit(sounding.extract_vad, local_paths[0]) if local_paths else None
        )

        futures = {
            ex.submit(
                _process_scan,
                i,
                workers,
                path,
                images_dir,
                zoom_lat,
                zoom_lon,
                zoom_km,
                radar_site_lat,
                radar_site_lon,
                event_name,
            ): i
            for i, path in enumerate(local_paths)
        }
        done = 0
        for fut in as_completed(futures):
            i = futures[fut]
            done += 1
            try:
                result = fut.result()
            except Exception as e:
                _p(f"    Failed to process {local_paths[i].name}: {e}")
                result = None
            elapsed = time.time() - t1
            _p(f"  Rendered scan {done} of {len(local_paths)} ({elapsed:.1f}s elapsed)")
            if result is not None:
                results[i] = result

    vad_data = None
    if vad_futures is not None:
        try:
            vad_data = vad_futures.result()
        except Exception as e:
            _p(f"    VAD extraction failed: {e}")

    # Reassemble in original scan order, not completion order
    radar_features = [results[i][0] for i in sorted(results)]
    radar_images = [results[i][1] for i in sorted(results)]

    print(f"  Total render time: {time.time() - t1:.1f}s for {len(local_paths)} scans")

    _p("Cleaning up scan files...")

    # Clean up raw scan files
    for path in local_paths:
        try:
            path.unlink()
        except Exception:
            pass

    return radar_features, radar_images, vad_data or {}


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
        "ncei_events": report.ncei_events,
        "sounding_indices": report.sounding_indices,
        "sounding_image": report.sounding_image,
        "hodograph_image": report.hodograph_image,
        "vad_srh": report.vad_srh,
        "outbreak_context": report.outbreak_context,
        "radar_features": report.radar_features,
        "radar_images": report.radar_images,
        "feature_notes": report.feature_notes,
        "lead_time": report.lead_time,
        "dat_tracks": report.dat_tracks,
        "spc_outlook": report.spc_outlook,
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
    zoom_km: float = 50.0,  # hard to gauge whats best, somewhere between 35km - 75km
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

    start_time = time.perf_counter()

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
            print(
                f"  Auto-scaled to {max_radar_scans} scans for {window_hours:.1f}h window"
            )

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
        vtec_events,
        auto_discover,
        avail,
        zoom_lat,
        zoom_lon,
        start,
        end,
        discover_phenomena,
        progress=progress,
    )

    # Step 1: fetch LSRs
    lsrs = _fetch_lsrs(avail, zoom_lat, zoom_lon, lsr_search_km, start, end, warnings)

    # Step 1b: fetch NCEI storm events

    ncei_events = _fetch_ncei(
        event_date=start.date(),
        location_display_name=location,
        warnings=warnings,
        progress=progress,
    )

    ncei_events = _filter_ncei_by_warnings(ncei_events, warnings)

    _progress(f"    NCEI: {len(ncei_events)} events after polygon filter")

    # Step 1d: billion-dollar disaster context
    event_date_obj = datetime.strptime(event_date, "%B %d, %Y").date()
    outbreak_context = billion_dollar.lookup(event_date_obj)
    if outbreak_context:
        _progress(f"  Matched outbreak: {outbreak_context['name']}")

    event_label = geocoding._get_event_city(location)

    # Step 2: process radar
    if avail.radar:
        radar_features, radar_images, vad_data = _process_radar(
            radar_site,
            start,
            end,
            max_radar_scans,
            images_dir,
            zoom_lat,
            zoom_lon,
            zoom_km,
            radar_site_lat,
            radar_site_lon,
            event_name=str(event_label),
            progress=progress,
        )
    else:
        print("  Skipping radar (no coverage for this event)")
        radar_features, radar_images, vad_data = [], [], {}

    # Step 2a: fetch pre-event sounding data - hodograph
    sounding_indices = {}
    sounding_image = None
    vad_srh = {}
    hodograph_image = None

    if avail.radar:
        raw_sounding = sounding.fetch_sounding(zoom_lat, zoom_lon, start)
        if raw_sounding:
            sounding_indices = sounding.compute_indices(raw_sounding)
            sounding_img_path = images_dir / "sounding_skewt.png"
            result = sounding.render_skewt(raw_sounding, sounding_img_path)
            if result:
                sounding_image = "images/sounding_skewt.png"
            if sounding_indices:
                _progress(
                    f"  Sounding: CAPE={sounding_indices.get('cape_jkg')} J/kg, "
                    f"Shear={sounding_indices.get('bulk_shear_06km_kt')} kt"
                )

        # VAD hodograph (extracted in _process_radar from the first scan)
        if vad_data:
            vad_srh = sounding.compute_srh(vad_data)
            hodo_path = images_dir / "hodograph.png"
            result = sounding.render_hodograph(vad_data, hodo_path)
            if result:
                hodograph_image = "images/hodograph.png"
            if vad_srh:
                _progress(
                    f"  VAD: 0-1km SRH={vad_srh.get('srh_01km')} m^2/s^2, "
                    f"0-3km SRH={vad_srh.get('srh_03km')} m^2/s^2"
                )
        else:
            _progress("  VAD: no wind data available")

    # Step 2b: compute lead time if possible
    lead_time = compute_lead_time(
        warnings, ncei_events, lsrs, local_timezone=local_timezone
    )

    if lead_time:
        _progress(
            f"  Computed lead time: {lead_time['lead_time_minutes']} minutes before event start"
        )
    else:
        _progress("  Could not compute lead time (missing or insufficient data)")

    # Step 2c: Fet DAT tornado tracks
    dat_tracks = {"polygons": [], "lines": []}
    try:
        zoom_bbox = (zoom_lon - 1.5, zoom_lat - 1.5, zoom_lon + 1.5, zoom_lat + 1.5)
        dat_tracks = dat.fetch_tornado_tracks(
            zoom_bbox,
            start,
            center_lat=zoom_lat,
            center_lon=zoom_lon,
        )
        total = len(dat_tracks["polygons"]) + len(dat_tracks["lines"])
        if total > 0:
            _progress(
                f"    Dat: {len(dat_tracks['lines'])} track(s), {len(dat_tracks['polygons'])} polygon(s)"
            )
        else:
            _progress("     Dat: no survey data available")
    except Exception as e:
        _progress(f"    Dat: fetch failed ({e})")

    # Step 2d: Fetch SPC Outlook overlay
    outlook = None
    try:
        outlook = spc_outlook.fetch_outlook(start)
        if outlook:
            features = outlook.get("features", [])
            labels = [
                f["properties"]["LABEL"]
                for f in features
                if f["properties"].get("LABEL") not in ("TSTM",)
            ]
            _progress(f"  SPC outlook: {', '.join(labels) if labels else 'TSTM only'}")
        else:
            _progress("  SPC outlook: no data available")
    except Exception as e:
        _progress(f"  SPC outlook: failed ({e})")

    # Step 3: assemble report + generate narrative
    report = EventReport(
        event_name=event_name,
        event_date=event_date,
        location=location,
        warnings=warnings,
        lsrs=lsrs,
        ncei_events=ncei_events,
        sounding_indices=sounding_indices,
        sounding_image=sounding_image,
        radar_features=radar_features,
        radar_images=radar_images,
        feature_notes=avail.notes,
        outbreak_context=outbreak_context,
        lead_time=lead_time,
        dat_tracks=dat_tracks,
        spc_outlook=outlook,
        hodograph_image=hodograph_image,
        vad_srh=vad_srh,
    )

    _progress("Generating narrative...")
    report.narrative = generate_narrative(report)

    # Step 4: write outputs
    _write_outputs(
        event_output_dir,
        slug,
        event_name,
        location,
        event_date,
        radar_site,
        report,
        local_timezone,
    )

    end_time = time.perf_counter()

    elapsed = end_time - start_time

    print(f"Execution Time: {elapsed:.6f} seconds")

    return event_output_dir