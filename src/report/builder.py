"""Assembles the final HTML report from structured data + LLM narrative."""

import json, re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
from collections import Counter
from shapely.geometry import shape, Point

from PIL import report
import markdown
from jinja2 import Environment, FileSystemLoader

from src.llm.base import get_client

TEMPLATES_DIR = Path(__file__).parent / "templates"
EF_RANK = {"EF0": 0, "EF1": 1, "EF2": 2, "EF3": 3, "EF4": 4, "EF5": 5, "EFU": -1}


@dataclass
class EventReport:
    """Bundled data for a single event - feeds both the LLM and the template."""

    event_name: str
    event_date: str
    location: str
    summary_metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    lsrs: list[dict[str, Any]] = field(default_factory=list)
    ncei_events: list[dict[str, Any]] = field(default_factory=list)
    sounding_indices: dict = field(default_factory=dict)
    sounding_image: str | None = None
    radar_features: list[dict[str, Any]] = field(default_factory=list)
    radar_images: list[str] = field(default_factory=list)
    feature_notes: list[str] = field(default_factory=list)
    narrative: str = ""
    outbreak_context: dict | None = None
    lead_time: dict | None = None
    dat_tracks: dict = field(default_factory=lambda: {"polygons": [], "lines": []})
    spc_outlook: dict | None = None
    hodograph_image: str | None = None
    vad_srh: dict = field(default_factory=dict)


SYSTEM_PROMPT = """You are a meteorologist writing a post-event severe weather report.

CRITICAL RULES:
1. Only reference values, times, and facts from the structured data provided.
2. Do not invent radar values, casualty counts, or any quantitative information.
3. Do not interpret radar imagery - use only the pre-extracted numeric features.
4. Do not include any statistics, damage estimates, or impact figures that are not explicitly present in the structured data provided.
4. If data is incomplete or ambiguous, say so explicitly rather than speculating.
5. Write in clear, professional prose suitable for a public-facing report.
6. SYNTHESIZE radar data - describe TRENDS (intensification, weakening, core descent)
   rather than listing every scan. The radar table renders separately; interpret it, don't transcribe it.
7. Ignore metadata notes about artifacts, capped values, or diagnostic flags.
8. Output HTML only. Use <h2>, <p>, <strong>, <ul>, <li>. No markdown, no **, no #.
9. CONFIDENCE: If a value seems inconsistent with other data, note the uncertainty.
   Example: "LSRs suggest EF3 damage though the NCEI survey was not yet available at time of writing."
10. LEAD TIME: If lead time data is provided, include a dedicated paragraph in the
    warnings section discussing warning effectiveness and lead time.
11. SOUNDING: If pre-event sounding data is provided, use it in the environmental context
    section. Reference CAPE, CIN, and bulk shear by name and explain their significance.
    The sounding station may be far from the event; treat the profile as regional context,
    not the storm's inflow environment.
12. SOUNDING LIMITATIONS: If CAPE appears low relative to the observed storm intensity,
    state plainly that the sounding likely under-samples the warm-sector instability
    (due to distance from the event, timing, or cold-season regimes where storms are
    shear-driven with modest CAPE). Do NOT invent warming or destabilization mechanisms.
    Never attribute instability growth to daytime surface heating for an event occurring
    at night or in the cold season.
13. Sounding WINDS: If Sounding wind profile data is provided, use it in the environmental context
    section alongside the sounding. Sounding winds represent the actual storm-time kinematic
    environment and are more current than the balloon sounding. Reference SRH by name
    and explain its role in mesocyclone development.
14. DATA HIERARCHY: When multiple sources provide the same metric, use this priority order:
    - Path length/width: DAT surveyed tracks > NCEI episode total > NCEI single county segment
    - EF rating: NCEI post-survey > LSR preliminary  
    - Fatalities/injuries: Use COMPUTED TOTALS, not individual NCEI records
    - Property damage: Suppress if flagged as incomplete
    Do not cite a single county's path length as the full track length.

Structure: overview -> environmental context (include sounding analysis here if available)
-> storm evolution -> warnings issued (include lead time discussion here) -> impacts -> conclusion.

IMPORTANT: Do not reduce detail in the impacts section to accommodate environmental context. 
Both sections should be equally thorough. The impacts section must reference specific 
NCEI county records, damage descriptions, and LSR data.
"""


def generate_narrative(report: EventReport) -> str:
    """Call the LLM to generate the narrative section."""
    client = get_client()

    lead_time = report.lead_time
    lead_time_section = ""
    if lead_time:
        if lead_time.get("data_quality") == "uncertain":
            lead_time_section = f"""
    WARNING LEAD TIME:
    Lead time could not be reliably determined (computed value: {lead_time['lead_time_minutes']} min,
    method: {lead_time.get('method')}).
    First warning issued: {lead_time['first_warning_utc']}
    Do not state a definitive lead time. Note in the warnings section that the lead time
    could not be reliably computed from the available data.
    """
        elif lead_time["had_warning"]:
            lead_time_section = f"""
    WARNING LEAD TIME:
    The first tornado warning was issued at {lead_time['first_warning_utc']},
    {lead_time['lead_time_minutes']} minutes BEFORE the highest-rated tornado touched down at {lead_time['first_touchdown_utc']}.
    NWS average lead time is ~13 minutes. Discuss warning effectiveness in the warnings section.
    """
        else:
            lead_time_section = f"""
    WARNING LEAD TIME:
    The highest-rated tornado touched down at {lead_time['first_touchdown_utc']},
    {abs(lead_time['lead_time_minutes'])} minutes BEFORE the first tornado warning was issued at {lead_time['first_warning_utc']}.
    This means there was NO advance warning for the most significant tornado. Note this in the warnings section.
    """

    outbreak_section = ""
    if report.outbreak_context:
        outbreak_section = f"""
OUTBREAK CONTEXT (NOAA Billion-Dollar Disasters):
This event was part of: {report.outbreak_context['name']}
Total outbreak damage: ${report.outbreak_context['cost_unadjusted']}M (unadjusted)
Total outbreak deaths: {report.outbreak_context['deaths']}
Note: This is outbreak-level data, not individual tornado damage.
"""

    sounding_section = ""
    if report.sounding_indices:
        s = report.sounding_indices

        # Compute how many hours before event the sounding is
        sounding_note = ""
        if s.get("valid"):
            try:
                from datetime import datetime, timezone

                sounding_dt = datetime.fromisoformat(s["valid"].replace("Z", "+00:00"))
                # event_start is available via report context - use first radar scan as proxy
                if report.radar_features:
                    first_scan = datetime.fromisoformat(
                        report.radar_features[0]["timestamp"].replace("Z", "+00:00")
                    )
                    hours_before = round(
                        (first_scan - sounding_dt).total_seconds() / 3600, 1
                    )
                    if hours_before > 1:
                        sounding_note = (
                            f"This sounding was taken approximately {hours_before} hours before the event "
                            f"and may not represent the storm inflow environment. "
                            f"Note any discrepancy factually; do not speculate about specific mechanisms "
                            f"(such as surface heating) unless the event occurred in the afternoon following "
                            f"a morning sounding."
                        )
                    else:
                        sounding_note = "This sounding was taken close to or during the event window."
            except Exception:
                pass

        sounding_section = f"""
    PRE-EVENT SOUNDING ({s.get('station')} valid {s.get('valid', '')[:19]}Z):
    Surface conditions: {s.get('sfc_temp_c')}°C temperature / {s.get('sfc_dewpoint_c')}°C dewpoint at {s.get('sfc_pressure_hpa')} hPa
    CAPE: {s.get('cape_jkg')} J/kg
    - <1000 = marginal, 1000-2500 = moderate, 2500-4000 = significant, >4000 = extreme instability
    CIN: {s.get('cin_jkg')} J/kg
    - Values near 0 = easy convection initiation, more negative = capped atmosphere
    0-6km Bulk Shear: {s.get('bulk_shear_06km_kt')} knots
    - <30kt = non-supercell, 30-40kt = supercell possible, >40kt = supercell favorable, >60kt = violent tornado potential
    {sounding_note}
    Use these values in the environmental context section to explain the atmospheric setup.
    """

    dat_section = ""
    if report.dat_tracks:
        lines = report.dat_tracks.get("lines", [])
        polygons = report.dat_tracks.get("polygons", [])
        dat_entries = lines + polygons
        if dat_entries:
            dat_section = """
    DAT SURVEYED TRACKS (NWS post-event damage survey - authoritative geometry):
    These are official NWS survey results. Prefer these over NCEI for path length and width.
    """
            for t in dat_entries:
                if t.get("ef_num", -1) < 0:
                    continue
                if not t.get("length_mi") or float(t.get("length_mi") or 0) <= 0:
                    continue  # skip non-track entries
                dat_section += f"""
    - {t.get('ef_scale')} | Event: {t.get('event_id') or 'unnamed'} | WFO: {t.get('wfo') or '-'}
    Path: {t.get('length_mi')} mi | Width: {t.get('width_yd')} yd | Max Wind: {t.get('max_wind') or '-'} mph
    Fatalities: {t.get('fatalities', 0)} | Injuries: {t.get('injuries', 0)}
    """

    vad_section = ""
    if report.vad_srh:
        v = report.vad_srh
        vad_section = f"""
    VAD WIND PROFILE (extracted from first radar scan - event-time atmospheric profile):
    0-1km SRH: {v.get('srh_01km')} m²/s²
    - <150 = weak rotation potential, 150-300 = moderate, 300-500 = significant, >500 = extreme
    0-3km SRH: {v.get('srh_03km')} m²/s²
    - >150 = supercell favorable, >300 = significant tornado potential
    Note: VAD winds are derived from the radar velocity field at event time - more representative
    of the actual storm environment than the pre-event balloon sounding.
    Use these values alongside the sounding to describe the kinematic environment.
    If SRH values are high, discuss their role in supporting mesocyclone development and tornado potential.
    """

    # Compute totals so LLM doesn't have to sum across county records
    total_deaths = sum(e.get("deaths_direct", 0) or 0 for e in report.ncei_events)
    total_injuries = sum(e.get("injuries_direct", 0) or 0 for e in report.ncei_events)
    tornado_count = sum(
        1 for e in report.ncei_events if e.get("event_type") == "Tornado"
    )

    # Find dominant episode
    tornado_events = [e for e in report.ncei_events if e.get("event_type") == "Tornado"]
    episodes = [e.get("episode_id") for e in tornado_events if e.get("episode_id")]
    dominant_episode = Counter(episodes).most_common(1)[0][0] if episodes else None

    # Sum path lengths within dominant episode
    episode_path_total = 0.0
    for e in report.ncei_events:
        if e.get("event_type") == "Tornado" and e.get("episode_id") == dominant_episode:
            try:
                episode_path_total += float(e.get("tor_length_mi") or 0)
            except (ValueError, TypeError):
                pass

    computed_totals = f"""
    COMPUTED TOTALS (pre-summed across all NCEI county records - use these figures, do not re-sum):
    Total direct deaths: {total_deaths}
    Total direct injuries: {total_injuries}
    Total tornado records: {tornado_count}
    Dominant episode ID: {dominant_episode}
    Total path length across all county segments of dominant episode: {round(episode_path_total, 1)} miles
    Note: NCEI records are split by county. Do not report a single county's path length as the total.
    Path lengths summed above represent all county segments of the same tornado system.
    Note: Do not reference episode IDs, event IDs, or other internal database identifiers in the narrative.
    """

    user_prompt = f"""Generate a post-event report narrative for the following event.

EVENT: {report.event_name}
DATE: {report.event_date}
LOCATION: {report.location}

--- AUTHORITATIVE SURVEY DATA ---

{dat_section}

{computed_totals}

NCEI STORM EVENTS ({len(report.ncei_events)} records - post-survey verified):
Prefer these over LSRs for EF ratings, path dimensions, casualties, and damage estimates.
NCEI records are split by county - use COMPUTED TOTALS and DAT for aggregate figures.
{json.dumps(report.ncei_events, indent=2, default=str)}

--- OBSERVATIONAL DATA ---

WARNINGS ISSUED ({len(report.warnings)} total):
{json.dumps(report.warnings, indent=2, default=str)}

LOCAL STORM REPORTS ({len(report.lsrs)} total):
{json.dumps(report.lsrs, indent=2, default=str)}

RADAR FEATURES (extracted from Level II volume scans):
Fields: timestamp, max reflectivity (dBZ), height of max reflectivity (kft AGL),
echo tops at 18/50 dBZ (kft), max inbound/outbound velocities (knots).
Interpret trends:
- Rising echo tops = updraft strengthening / overshooting tops.
- High-reflectivity core descent (Max dBZ spikes while height of max reflectivity drops) = significant hail core descent or low-level debris ball signature during maximum tornadic intensity.
- Strong velocity couplet = mesocyclone rotation.
CRITICAL RADAR CONSTRAINT:
If max inbound and outbound velocities show identical, repeating values across multiple timestamps (e.g., exactly 65.1 knots),
do NOT describe the rotation as "static" or "stable." Interpret this as the mesocyclone completely saturating or
exceeding the radar's maximum unambiguous velocity threshold (the Nyquist limit),
proving the actual rotational winds were higher than the instrument could natively measure.
{json.dumps(report.radar_features, indent=2, default=str)}

--- ATMOSPHERIC CONTEXT ---

{sounding_section}

{vad_section}

{outbreak_section}

--- TIMING & WARNINGS ---

{lead_time_section}

Write the narrative HTML now. Cite specific timestamps and values. Only use data above."""

    raw = client.generate(SYSTEM_PROMPT, user_prompt, max_tokens=8192)
    return markdown.markdown(raw, extensions=["extra", "sane_lists"])


def _format_time_with_local_tz(iso_str: str, local_tz: str) -> str:
    """Format ISO timestamp as 'HH:MM UTC (H:MM AM/PM TZ)'."""
    if not iso_str:
        return ""
    try:
        # Handled both Z and offset-aware timestamps
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        utc_part = dt.strftime("%H:%M UTC")
        if local_tz == "UTC":
            return utc_part
        local_dt = dt.astimezone(ZoneInfo(local_tz))
        local_part = local_dt.strftime("%I:%M %p %Z").lstrip("0")
        return f"{utc_part} ({local_part})"
    except (ValueError, TypeError):
        return iso_str  # Return raw string if parsing fails


def _ncei_for_map(ncei_events: list) -> list:
    """Strip heavy fields not needed for map rendering."""
    return [
        {
            "event_type": e.get("event_type"),
            "begin_lat": e.get("begin_lat"),
            "begin_lon": e.get("begin_lon"),
            "end_lat": e.get("end_lat"),
            "end_lon": e.get("end_lon"),
            "tor_f_scale": e.get("tor_f_scale"),
            "tor_length_mi": e.get("tor_length_mi"),
            "tor_width_yd": e.get("tor_width_yd"),
            "county": e.get("county"),
            "begin_time": e.get("begin_time"),
            "deaths_direct": e.get("deaths_direct", 0),
            "deaths_indirect": e.get("deaths_indirect", 0),
            "injuries_direct": e.get("injuries_direct", 0),
            "injuries_indirect": e.get("injuries_indirect", 0),
            "damage_property": e.get("damage_property"),
        }
        for e in ncei_events
    ]


def build_html_string(report: EventReport, local_timezone: str = "UTC") -> str:
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True)
    env.filters["fmt_time"] = lambda s: _format_time_with_local_tz(s, local_timezone)
    template = env.get_template("report.html")
    return template.render(
        report=report,
        summary=compute_summary(report),
        ncei_for_map=_ncei_for_map(report.ncei_events),
    )


def _ncei_tzinfo(event: dict):
    """NCEI CZ_TIMEZONE is a fixed offset like 'CST-6', no DST."""
    tz_str = event.get("cz_timezone", "") or ""
    m = re.search(r"(-?\d+)\s*$", tz_str)
    if m:
        return timezone(timedelta(hours=int(m.group(1))))
    return None  # fall back to UTC or flag


def _ncei_sort_ts(e: dict) -> float:
    dt = _parse_ncei_dt(e.get("begin_time", ""))
    return dt.timestamp() if dt else float("inf")


def _select_warning_for_touchdown(tornado_warnings, touchdown_pt, touchdown_utc):
    """Earliest-issued tornado warning whose polygon contains the touchdown
    point and which was in effect at touchdown time."""
    covering = []
    for w in tornado_warnings:
        if not (w.get("issued_at") and w.get("expires_at") and w.get("polygon")):
            continue
        try:
            issued = datetime.fromisoformat(w["issued_at"].replace("Z", "+00:00"))
            expired = datetime.fromisoformat(w["expires_at"].replace("Z", "+00:00"))
        except (ValueError, TypeError) as e:
            print(f"  >>> parse fail {w.get('vtec_id')}: {e} raw={w['issued_at']!r}")
            continue
        if issued.tzinfo is None:
            issued = issued.replace(tzinfo=ZoneInfo("UTC"))
        if expired.tzinfo is None:
            expired = expired.replace(tzinfo=ZoneInfo("UTC"))
        in_window = issued <= touchdown_utc <= expired
        try:
            contains = shape(w["polygon"]).contains(touchdown_pt)
        except Exception as e:
            print(f"  >>> shape fail {w.get('vtec_id')}: {e}")
            continue
        print(
            f"  >>> {w.get('vtec_id')}: issued={issued} expired={expired} "
            f"touchdown={touchdown_utc} in_window={in_window} contains={contains}"
        )
        if in_window and contains:
            covering.append((issued, w))
    if not covering:
        return None
    return min(covering, key=lambda x: x[0])


def compute_lead_time(
    warnings: list, ncei_events: list, lsrs: list, local_timezone: str = "UTC"
) -> dict | None:

    tornado_warnings = [
        w
        for w in warnings
        if w.get("phenomena") == "TO" and w.get("significance") == "W"
    ]
    if not tornado_warnings:
        return None

    ef_rank = {"EF0": 0, "EF1": 1, "EF2": 2, "EF3": 3, "EF4": 4, "EF5": 5, "EFU": -1}

    # Find dominant episode - most tornado records share this episode ID
    tornado_events = [e for e in ncei_events if e.get("event_type") == "Tornado"]
    episodes = [e.get("episode_id") for e in tornado_events if e.get("episode_id")]
    dominant_episode = Counter(episodes).most_common(1)[0][0] if episodes else None

    candidates = (
        [e for e in tornado_events if e.get("episode_id") == dominant_episode]
        if dominant_episode
        else tornado_events
    )

    # Rank: strongest EF first, then earliest touchdown. Iterate until we find
    # a segment whose touchdown we have warning-polygon coverage for.
    ranked = sorted(
        candidates,
        key=lambda e: (
            -ef_rank.get(e.get("tor_f_scale", ""), -1),
            _ncei_sort_ts(e),
        ),
    )

    for i, cand in enumerate(ranked):
        print(
            f"  >>> candidate {i}: EF={cand.get('tor_f_scale')} begin={cand.get('begin_time')} "
            f"{cand.get('county')},{cand.get('state')}"
        )

    selected = None
    method = "window_min"
    touchdown_utc = None
    touchdown_pt = None

    for cand in ranked:
        if not cand.get("begin_time"):
            continue
        dt = _parse_ncei_dt(cand["begin_time"])
        if not dt:
            continue
        tz = _ncei_tzinfo(cand)
        if tz is None:
            tz = ZoneInfo(local_timezone)
        td_utc = dt.replace(tzinfo=tz).astimezone(ZoneInfo("UTC"))

        lat = cand.get("begin_lat")
        lon = cand.get("begin_lon")
        if lat is None or lon is None:
            # Keep as a touchdown-time fallback even without a point
            if touchdown_utc is None:
                touchdown_utc = td_utc
            continue
        pt = Point(float(lon), float(lat))

        # Remember the best-ranked candidate as fallback touchdown
        if touchdown_utc is None:
            touchdown_utc = td_utc
            touchdown_pt = pt

        result = _select_warning_for_touchdown(tornado_warnings, pt, td_utc)
        if result:
            issued, warning = result
            selected = (issued, warning)
            touchdown_utc = td_utc
            touchdown_pt = pt
            method = "polygon_match"
            break

    # Fall back to LSRs for touchdown time/point if NCEI gave us nothing
    if touchdown_utc is None:
        for l in lsrs:
            if l.get("event") == "TORNADO" and l.get("time"):
                try:
                    dt = datetime.fromisoformat(l["time"].replace("Z", "+00:00"))
                    touchdown_utc = (
                        dt.astimezone(ZoneInfo("UTC"))
                        if dt.tzinfo
                        else dt.replace(tzinfo=ZoneInfo("UTC"))
                    )
                    if l.get("lat") is not None and l.get("lon") is not None:
                        touchdown_pt = Point(float(l["lon"]), float(l["lat"]))
                        result = _select_warning_for_touchdown(
                            tornado_warnings, touchdown_pt, touchdown_utc
                        )
                        if result:
                            issued, warning = result
                            selected = (issued, warning)
                            method = "polygon_match"
                    break
                except Exception:
                    continue

    if touchdown_utc is None:
        return None

    print(
        f"  >>> touchdown_pt: {touchdown_pt}, method: {method}, "
        f"warnings with polygon: {sum(1 for w in tornado_warnings if w.get('polygon'))}"
    )

    if selected is None:
        # No polygon contained any candidate touchdown (pre-2007 event,
        # missing geometry, discovery gap, or genuinely unwarned).
        try:
            first_warning_str = min(
                w["issued_at"] for w in tornado_warnings if w.get("issued_at")
            )
            issued = datetime.fromisoformat(first_warning_str.replace("Z", "+00:00"))
            selected = (issued, None)
        except (ValueError, TypeError):
            return None

    issued, warning = selected
    lead_minutes = (touchdown_utc - issued).total_seconds() / 60

    out = {
        "lead_time_minutes": round(lead_minutes),
        "first_warning_utc": issued.isoformat(),
        "first_touchdown_utc": touchdown_utc.isoformat(),
        "had_warning": lead_minutes > 0,
        "method": method,
    }

    if method != "polygon_match" or not (-5 <= lead_minutes <= 120):
        out["data_quality"] = "uncertain"
        if lead_minutes < -5:
            out["had_warning"] = False

    return out


def _parse_ncei_dt(value: str) -> datetime | None:
    """Parse NCEI datetime strings."""
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


def compute_summary(report: EventReport) -> dict:
    """Compute impact summary stats for the report template."""
    max_ef = None
    max_ef_rank = -1
    total_deaths = 0
    total_injuries = 0
    total_damage = 0.0
    max_path = 0.0
    total_path = 0.0  # Added to track overall episode footprint

    # Determine the dominant hazard for this report
    type_counts = Counter(e.get("event_type") for e in report.ncei_events)
    has_tornado = type_counts.get("Tornado", 0) > 0

    if has_tornado:
        impact_types = {"Tornado"}
        impact_label = "tornadoes"
    else:
        # Non-tornado event: count the convective wind/hail family
        impact_types = {
            "Thunderstorm Wind",
            "Hail",
            "Flash Flood",
            "Strong Wind",
            "High Wind",
            "Lightning",
        }
        impact_label = "severe weather"

    # Combined into a single loop over events for efficiency
    for e in report.ncei_events:
        in_scope = e.get("event_type") in impact_types
        if in_scope:
            total_deaths += e.get("deaths_direct", 0) or 0
            total_injuries += e.get("injuries_direct", 0) or 0
            if e.get("damage_property"):
                total_damage += e["damage_property"]

            if e.get("tor_length_mi"):
                try:
                    length = float(e["tor_length_mi"])
                    total_path += length  # Sum everything in the county episode
                    if length > max_path:
                        max_path = length
                except (ValueError, TypeError):
                    pass

            # Handle normalization for legacy 'F' scales and modern 'EF' scales
            raw_scale = e.get("tor_f_scale")
            if raw_scale:
                # Clean string (e.g., "F5 " or "EF5" -> "5")
                clean_rating = str(raw_scale).strip().replace("EF", "").replace("F", "")
                try:
                    # Use the raw integer digit (0-5) as the rank baseline
                    rank = int(clean_rating)
                    if rank > max_ef_rank:
                        max_ef_rank = rank
                        max_ef = str(
                            raw_scale
                        ).strip()  # Preserves "F5" or "EF5" for the UI
                except (ValueError, TypeError):
                    pass

    radar_dbz = [
        f["max_reflectivity_dbz"]
        for f in report.radar_features
        if f.get("max_reflectivity_dbz") is not None
    ]
    radar_tops = [
        f["echo_top_18dbz_kft"]
        for f in report.radar_features
        if f.get("echo_top_18dbz_kft") is not None
    ]

    # Suppress damage if clearly incomplete relative to event severity
    if max_ef_rank >= 3 and total_damage < 100_000:
        total_damage = None
    elif total_damage < 10_000:
        total_damage = None

    # DAT path length takes priority over NCEI
    dat_path_mi = None
    path_source = "NCEI"
    if report.dat_tracks:
        # Path length must come from centerline features only; damage-polygon
        # length_mi describes a swath segment, not the track.
        line_lengths = [
            float(t["length_mi"])
            for t in report.dat_tracks.get("lines", [])
            if t.get("length_mi") and float(t.get("length_mi") or 0) > 0
        ]
        if line_lengths:
            dat_path_mi = round(max(line_lengths), 1)
            path_source = "DAT"

    print(
        f"  >>> DAT lines: {[(t.get('length_mi'), t.get('ef_num')) for t in report.dat_tracks.get('lines', [])]}"
    )

    if dat_path_mi is not None:
        max_path_mi = dat_path_mi
    elif max_path > 0:
        max_path_mi = round(max_path, 1)
    else:
        max_path_mi = None

    return {
        "max_ef": max_ef,
        "total_deaths": total_deaths,
        "impact_label": impact_label,
        "total_injuries": total_injuries,
        "total_damage": total_damage,
        "max_path_mi": max_path_mi,
        "path_source": path_source,
        "total_path_mi": round(total_path, 1) if total_path > 0 else None,
        "max_dbz": round(max(radar_dbz), 1) if radar_dbz else None,
        "max_tops": round(max(radar_tops), 1) if radar_tops else None,
        "warning_count": len(report.warnings),
        "lsr_count": len(report.lsrs),
        "outbreak_context": report.outbreak_context,
        "lead_time": report.lead_time,
    }
