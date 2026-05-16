"""Event configuration loader."""
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.models import VTECEventRef

EVENTS_DIR = Path(__file__).parent.parent / "events"


@dataclass
class EventConfig:
    """Parameters for running the pipeline on a single event."""
    event_name: str
    event_date: str
    location: str
    window_start_utc: datetime
    window_end_utc: datetime
    local_timezone: str = "UTC"
    radar_site: str | None = None
    zoom_lat: float | None = None
    zoom_lon: float | None = None
    zoom_km: float = 60.0
    lsr_search_km: float = 75.0
    max_radar_scans: int = 8
    vtec_events: list[VTECEventRef] = field(default_factory=list)
    # If true, auto-discover VTEC events from zoom_lat/zoom_lon + window
    auto_discover: bool = False
    discover_phenomena: tuple[str, ...] = ("TO", "SV")


def _expand_vtec_event(item: dict) -> list[VTECEventRef]:
    """Expand a single VTEC entry, supporting either etn or etn_range."""
    base = {k: item[k] for k in ("wfo", "year", "phenomena", "significance")}
    if "etn_range" in item:
        start, end = item["etn_range"]
        return [VTECEventRef(**base, etn=n) for n in range(start, end + 1)]
    if "etn" in item:
        return [VTECEventRef(**base, etn=item["etn"])]
    raise ValueError(f"VTEC entry needs 'etn' or 'etn_range': {item}")


def load_event(slug_or_path: str | Path) -> EventConfig:
    """Load an event config from JSON."""
    path = Path(slug_or_path)
    if not path.suffix:
        path = EVENTS_DIR / f"{slug_or_path}.json"
    if not path.exists():
        raise FileNotFoundError(f"Event config not found: {path}")

    with open(path) as f:
        data = json.load(f)

    vtec_events = []
    for v in data.get("vtec_events", []):
        vtec_events.extend(_expand_vtec_event(v))

    auto_discover = data.get("auto_discover", False) or "discover" in data
    discover_pheno = tuple(
        data.get("discover", {}).get("phenomena", ["TO", "SV"])
    )

    return EventConfig(
        event_name=data["event_name"],
        event_date=data["event_date"],
        location=data["location"],
        radar_site=data.get("radar_site"),
        window_start_utc=datetime.fromisoformat(data["window_start_utc"].replace("Z", "+00:00")),
        window_end_utc=datetime.fromisoformat(data["window_end_utc"].replace("Z", "+00:00")),
        local_timezone=data.get("local_timezone", "UTC"),
        zoom_lat=data.get("zoom_lat"),
        zoom_lon=data.get("zoom_lon"),
        zoom_km=data.get("zoom_km", 60.0),
        lsr_search_km=data.get("lsr_search_km", 75.0),
        max_radar_scans=data.get("max_radar_scans", 8),
        vtec_events=vtec_events,
        auto_discover=auto_discover,
        discover_phenomena=discover_pheno,
    )
    
def list_events() -> list[str]:
    """List all event slugs available in the events/ directory."""
    if not EVENTS_DIR.exists():
        return []
    return sorted(p.stem for p in EVENTS_DIR.glob("*.json"))