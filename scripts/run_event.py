"""Run the pipeline on any event defined in events/.

Usage:
    python -m scripts.run_event joplin_2011
    python -m scripts.run_event moore_2013
    python -m scripts.run_event --list
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse

from src.event_config import load_event, list_events
from src.pipeline import run_pipeline


def main():
    parser = argparse.ArgumentParser(description="Generate a severe weather event report.")
    parser.add_argument(
        "event",
        nargs="?",
        help="Event slug (e.g., joplin_2011) or path to event config JSON",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all available event configs",
    )
    
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate report even if it already exists",
    )
    
    args = parser.parse_args()

    if args.list:
        events = list_events()
        if not events:
            print("No events found in events/ directory.")
            return
        print("Available events:")
        for slug in events:
            print(f"  {slug}")
        return

    if not args.event:
        parser.error("Specify an event slug or use --list")

    print(f"Loading event config: {args.event}")
    cfg = load_event(args.event)

    print(f"Running pipeline for: {cfg.event_name} ({cfg.event_date})")
    output = run_pipeline(
        event_name=cfg.event_name,
        event_date=cfg.event_date,
        location=cfg.location,
        radar_site=cfg.radar_site, # type: ignore
        start=cfg.window_start_utc,
        end=cfg.window_end_utc,
        vtec_events=cfg.vtec_events,
        max_radar_scans=cfg.max_radar_scans,
        zoom_lat=cfg.zoom_lat,
        zoom_lon=cfg.zoom_lon,
        zoom_km=cfg.zoom_km,
        lsr_search_km=cfg.lsr_search_km,
        local_timezone=cfg.local_timezone,
        auto_discover=cfg.auto_discover,
        discover_phenomena=cfg.discover_phenomena,
        force=args.force,
    )
    print(f"Done. Report: {output / 'report_data.json'}")


if __name__ == "__main__":
    main()