"""End-to-end pipeline: event params in, HTML report out."""
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from src import config
from src.data_sources import iem, nexrad
from src.radar import processor as radar_processor
from src.radar import renderer as radar_renderer
from src.report.builder import EventReport, build_html, generate_narrative
from src.data_sources import discovery, geocoding, radar_locator
from src.models import VTECEventRef
from src.feature_gates import feature_availability


def run_pipeline(
    event_name: str,
    event_date: str,
    location: str,
    radar_site: str,
    start: datetime,
    end: datetime,
    vtec_events: list[VTECEventRef] | None = None,
    max_radar_scans: int = 8,
    zoom_lat: float | None = None,
    zoom_lon: float | None = None,
    zoom_km: float = 60.0,
    lsr_search_km: float = 75.0,
    local_timezone: str = "UTC",
    auto_discover: bool = False,
    discover_phenomena: tuple[str, ...] = ("TO", "SV"),
    output_path: Path | None = None,
) -> Path:
    """Run the full pipeline for one event."""
    report = EventReport(
        event_name=event_name,
        event_date=event_date,
        location=location,
    )

    # Step 0: geocode location if coords missing
    if zoom_lat is None or zoom_lon is None:
        print(f"  Geocoding location: {location}...")
        geo = geocoding.geocode(location)
        if geo is None:
            raise ValueError(f"Could not geocode location: {location}")
        zoom_lat, zoom_lon = geo.lat, geo.lon
        print(f"    => {zoom_lat:.3f}, {zoom_lon:.3f} ({geo.display_name})")
        
    slug = event_name.lower().replace(" ", "_")
    event_output_dir = config.OUTPUT_DIR / slug
    event_output_dir.mkdir(parents=True, exist_ok=True)
    images_dir = event_output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    
    # Step 0a: auto-select radar if not provided
    if radar_site is None:
        print(f"  Finding nearest NEXRAD to ({zoom_lat:.3f}, {zoom_lon:.3f})...")
        nearest = radar_locator.find_nearest_radar(zoom_lat, zoom_lon)
        if nearest is None:
            raise ValueError(f"No NEXRAD site within range of ({zoom_lat}, {zoom_lon})")
        radar_site = nearest.icao
        radar_commissioned = nearest.commissioned
        print(f"    => {radar_site}: {nearest.name} ({nearest.distance_km}km)")
    else:
        radar_commissioned = None  # unknown
        for s in radar_locator._load_stations():
            if s["icao"] == radar_site:
                radar_commissioned = s.get("commissioned")
                break
            
    # Step 0b: Gate features based on event date and radar
    print(f"Checking feature availability for {start.date()} ... ")
    avail = feature_availability(start.date(), radar_commissioned)
    report.feature_notes = avail.notes
    if avail.notes:
        for note in avail.notes:
            print(f"  - {note}")
            
    # Step 0c: auto discover warnings
    if auto_discover and not vtec_events:
        if avail.swb_polygons:
            print(f"  Discovering warnings at ({zoom_lat:.3f}, {zoom_lon:.3f})...")
            vtec_events = discovery.discover_events(
                lat=zoom_lat,
                lon=zoom_lon,
                start=start,
                end=end,
                phenomena=discover_phenomena,
            )
            print(f"   => Found {len(vtec_events)} matching events")
        else:
            print("  Skipping warning auto-discovery (predates polygon warnings)")
            vtec_events = []
    

    # Step 1: warnings from IEM
    if vtec_events:
        for ref in vtec_events:
            print(f"  Fetching {ref.wfo}.{ref.phenomena}.{ref.significance}.{ref.etn}...")
            warning = iem.fetch_event_bundle(
                ref.wfo, ref.year, ref.phenomena, ref.significance, ref.etn
            )
            if warning:
                report.warnings.append(warning)

    # Step 1b: comprehensive LSR fetch by bbox
    # Use a wider radius than the radar zoom (storm paths extend beyond)
    # and a wider time window (LSRs lag the event by hours, surveys days)
    if avail.iem_lsr and zoom_lat is not None and zoom_lon is not None:
        deg_per_km_lat = 1 / 111.0
        deg_per_km_lon = 1 / (111.0 * abs(math.cos(math.radians(zoom_lat))))
        lat_pad = lsr_search_km * deg_per_km_lat
        lon_pad = lsr_search_km * deg_per_km_lon

        lsr_start = start - timedelta(hours=1)
        lsr_end = end + timedelta(hours=6)
        sts = lsr_start.strftime("%Y-%m-%dT%H:%MZ")
        ets = lsr_end.strftime("%Y-%m-%dT%H:%MZ")

        print(f"  Fetching LSRs within {lsr_search_km:.0f}km of {zoom_lat:.3f}, {zoom_lon:.3f}...")
        bbox_lsrs = iem.fetch_lsrs_by_bbox(
            sts=sts,
            ets=ets,
            west=zoom_lon - lon_pad,
            east=zoom_lon + lon_pad,
            south=zoom_lat - lat_pad,
            north=zoom_lat + lat_pad,
        )
        print(f"    Got {len(bbox_lsrs)} LSRs")

        seen_lsrs: set[tuple] = set()
        for lsr in bbox_lsrs:
            key = (lsr.get("time"), lsr.get("lat"), lsr.get("lon"), lsr.get("event"))
            if key not in seen_lsrs:
                seen_lsrs.add(key)
                report.lsrs.append(lsr)
    elif zoom_lat is not None and zoom_lon is not None:
        print(" Skipping LSR fetch as data is not reliably archived for this time period")
        
    # Step 2-3: radar
    if avail.radar:
        print(f"  Listing radar scans for {radar_site} between {start} and {end}...")
        all_scans = nexrad.list_scans(radar_site, start, end)
        print(f"    Found {len(all_scans)} scans, selecting up to {max_radar_scans}...")
        key_scans = nexrad.pick_key_scans(all_scans, max_scans=max_radar_scans)

        print(f"  Downloading {len(key_scans)} scans...")
        local_paths = nexrad.download_scans(key_scans)

        print(f"  Extracting features and rendering images from {len(local_paths)} scans...")
        for path in local_paths:
            try:
                features = radar_processor.extract_features(path)
                report.radar_features.append(features.to_dict())
                print(f"    {features.timestamp}: {features.max_reflectivity_dbz} dBZ, top {features.echo_top_18dbz_kft} kft")

                ts_safe = features.timestamp.replace(":", "-").replace(".", "-")[:19]
                img_path = images_dir / f"{ts_safe}_reflectivity.png"
                radar_renderer.render_radar_panel(
                    path,
                    img_path,
                    center_lat=zoom_lat,
                    center_lon=zoom_lon,
                    zoom_km=zoom_km,
                )
                report.radar_images.append(f"images/{img_path.name}")
                print(f"      => rendered {img_path.name}")
            except Exception as e:
                print(f"    Failed to process {path.name}: {e}")
    else:
        print(" Skipping radar processing as no radar data is available for this time period")

    print("  Generating narrative...")
    report.narrative = generate_narrative(report)

    if output_path is None:
        output_path = event_output_dir / "index.html"

    build_html(report, output_path, local_timezone=local_timezone)
    return output_path