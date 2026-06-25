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


SYSTEM_PROMPT_BASE = """You are a meteorologist writing a post-event severe weather report.

CRITICAL RULES:
1. Only reference values, times, and facts from the structured data provided.
2. Do not invent radar values, casualty counts, or any quantitative information.
3. Do not interpret radar imagery. Use only the pre-extracted numeric features.
4. Do not include any statistics, damage estimates, or impact figures that are not
   explicitly present in the structured data provided.
5. If data is incomplete or ambiguous, say so explicitly rather than speculating.
6. Write in clear, professional prose suitable for a public-facing report.
7. SYNTHESIZE radar data. Describe TRENDS (intensification, weakening, core descent)
   rather than listing every scan. The radar table renders separately. Interpret it,
   do not transcribe it.
8. Describe only the radar trends actually present in this event's scans. Do not define
   radar concepts generically, and do not mention a signature (core descent, velocity
   couplet, overshooting top, hail core) unless this event's data shows it.
9. Ignore metadata notes about artifacts, capped values, or diagnostic flags.
10. Output HTML only. Use <h2>, <p>, <strong>, <ul>, <li>. No markdown, no **, no #.
11. CONFIDENCE: If a value seems inconsistent with other data, note the uncertainty.
    Example: "LSRs suggest EF3 damage though the NCEI survey was not yet available at
    time of writing."

Structure: overview -> environmental context -> storm evolution -> warnings issued
-> impacts -> conclusion.

IMPORTANT: Do not reduce detail in the impacts section to accommodate environmental
context. Both sections should be equally thorough. The impacts section must reference
specific NCEI county records, damage descriptions, and LSR data where available.
"""


EVENT_MODULE_TORNADO = """
TORNADO EVENT GUIDANCE (applies in addition to the base rules):

This event produced one or more tornadoes. The tornado is the headline even if wind or
hail reports outnumber it. Lead with the tornado threat, then cover accompanying wind/hail.

LEAD TIME: If lead time data is provided, include a dedicated paragraph in the warnings
section discussing warning effectiveness and lead time.

SOUNDING: If pre-event sounding data is provided, use it in the environmental context
section. Reference CAPE, CIN, and bulk shear by name and explain their significance.
The sounding station may be far from the event. Treat the profile as regional context,
not the storm's inflow environment.

SOUNDING LIMITATIONS: If CAPE appears low relative to the observed storm intensity,
state plainly that the sounding likely under-samples the warm-sector instability (due
to distance from the event, timing, or cold-season regimes where storms are shear-driven
with modest CAPE). Do NOT invent warming or destabilization mechanisms. Never attribute
instability growth to daytime surface heating for an event occurring at night or in the
cold season.

SOUNDING WINDS: If sounding wind profile data is provided, use it in the environmental
context section alongside the sounding. Sounding winds represent the actual storm-time
kinematic environment and are more current than the balloon sounding. Reference SRH by
name and explain its role in mesocyclone development.

DATA HIERARCHY: When multiple sources provide the same metric, use this priority order:
- Path length/width: DAT surveyed tracks > NCEI episode total > NCEI single county segment
- EF rating: NCEI post-survey > LSR preliminary
- Fatalities/injuries: Use COMPUTED TOTALS, not individual NCEI records
- Property damage: Suppress if flagged as incomplete
Do not cite a single county's path length as the full track length.
"""


EVENT_MODULE_CONVECTIVE = """
CONVECTIVE (WIND / HAIL) EVENT GUIDANCE (applies in addition to the base rules):

This event is convective but NOT tornadic: severe thunderstorm wind and/or hail. Frame
it as a severe thunderstorm event. There is no tornado in the verified data.

- Do NOT use EF-scale ratings, path length/width, touchdown times, or tornado/mesocyclone
  framing. There is no touchdown and no tornado lead-time discussion.
- Report PEAK hail size (inches) and PEAK wind gust from the COMPUTED TOTALS, and support
  them with specific NCEI county records and LSRs. NCEI MAGNITUDE is hail diameter in
  inches for Hail records and gust speed in knots for Thunderstorm Wind records.
  MAGNITUDE_TYPE distinguishes estimated gusts (EG) from measured gusts (MG/MS).
- Discuss storm mode where the data supports it: organized lines/derecho-type wind events
  vs discrete cells producing large hail. Do not assert a mode the radar trends do not show.

SOUNDING: If pre-event sounding data is provided, use it in the environmental context
section. Reference CAPE, CIN, and bulk shear by name. High CAPE supports strong updrafts
and large hail. Strong deep-layer shear supports organized and supercellular storm modes.
The sounding station may be far from the event. Treat the profile as regional context.

SOUNDING LIMITATIONS: If CAPE appears low relative to the observed storm intensity, state
plainly that the sounding likely under-samples the warm-sector instability (due to distance
or timing). Do NOT invent warming or destabilization mechanisms. Never attribute instability
growth to daytime surface heating for an event occurring at night or in the cold season.

STORM-RELATIVE HELICITY: If SRH is provided and elevated, note that it indicates supercell
potential, which is relevant to large-hail and damaging-wind production. Do NOT extend SRH
into tornado claims. No tornado was verified in this event.

DATA HIERARCHY: Prefer NCEI post-survey magnitudes over preliminary LSR magnitudes. Use
COMPUTED TOTALS for peak values. Do not re-derive maxima by scanning county records.
"""


EVENT_MODULE_BAIL = """
NON-CONVECTIVE EVENT GUIDANCE (dominant event type: {dominant_type}):

This event is outside the tool's convective scope. Report only what is present in the
data and keep the report proportionate. Do not pad.

- Report this event on its own terms, using ONLY the metrics present in the NCEI records,
  LSRs, and radar features provided.
- Do NOT introduce tornado, supercell, mesocyclone, EF-scale, hail-core, or severe-storm
  framing unless matching survey data appears in the data.
- Do NOT introduce thermodynamic indices (CAPE, CIN, SRH, bulk shear). They are not
  provided for this event and do not apply.
- Sounding values (CAPE, CIN, SRH, shear) may be displayed for completeness but do
  NOT characterize this event. Do not infer instability, storm mode, supercell
  potential, or sounding under-sampling from them. This is not a convective event.
- The warnings array may be empty for this event type. Do not infer or invent warnings
  that are not listed. If no warnings are present, say so plainly.
"""


# --- Classification sets. ---
TORNADO_TYPES = {"Tornado", "Funnel Cloud"}
CONVECTIVE_TYPES = {
    "Thunderstorm Wind",
    "Hail",
    "Marine Thunderstorm Wind",
    "Marine Hail",
    "Lightning",
}


def generate_narrative(report: EventReport) -> str:
    """Call the LLM to generate the narrative section."""
    client = get_client()

    # --- Classify the event (tiered) ---
    type_counts = Counter(
        e.get("event_type") for e in report.ncei_events if e.get("event_type")
    )
    dominant_type = type_counts.most_common(1)[0][0] if type_counts else "Unknown"
    tornado_count = sum(
        1 for e in report.ncei_events if e.get("event_type") in TORNADO_TYPES
    )

    if tornado_count > 0:
        event_class = "Tornadic"
        event_module = EVENT_MODULE_TORNADO
        is_tornadic = True
        is_convective = True
    elif dominant_type in CONVECTIVE_TYPES:
        event_class = "Convective (wind/hail)"
        event_module = EVENT_MODULE_CONVECTIVE
        is_tornadic = False
        is_convective = True
    else:
        event_class = dominant_type
        event_module = EVENT_MODULE_BAIL.format(dominant_type=dominant_type)
        is_tornadic = False
        is_convective = False

    system_prompt = SYSTEM_PROMPT_BASE + event_module

    # --- Lead time (tornado-only) ---
    lead_time = report.lead_time
    lead_time_section = ""
    if lead_time and is_tornadic:
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
Note: This is outbreak-level data, not individual storm damage.
"""

    # --- Sounding (any convective event: CAPE/shear apply to wind/hail too) ---
    sounding_section = ""
    if report.sounding_indices and is_convective:
        s = report.sounding_indices

        sounding_note = ""
        if s.get("valid"):
            try:
                from datetime import datetime, timezone

                sounding_dt = datetime.fromisoformat(s["valid"].replace("Z", "+00:00"))
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
                            f"Note any discrepancy factually. Do not speculate about specific mechanisms "
                            f"(such as surface heating) unless the event occurred in the afternoon following "
                            f"a morning sounding."
                        )
                    else:
                        sounding_note = "This sounding was taken close to or during the event window."
            except Exception:
                pass

        sounding_section = f"""
    PRE-EVENT SOUNDING ({s.get('station')} valid {s.get('valid', '')[:19]}Z):
    Surface conditions: {s.get('sfc_temp_c')}C temperature / {s.get('sfc_dewpoint_c')}C dewpoint at {s.get('sfc_pressure_hpa')} hPa
    CAPE: {s.get('cape_jkg')} J/kg
    - <1000 = marginal, 1000-2500 = moderate, 2500-4000 = significant, >4000 = extreme instability
    CIN: {s.get('cin_jkg')} J/kg
    - Values near 0 = easy convection initiation, more negative = capped atmosphere
    0-6km Bulk Shear: {s.get('bulk_shear_06km_kt')} knots
    - <30kt = non-supercell, 30-40kt = supercell possible, >40kt = supercell favorable, >60kt = violent storm potential
    {sounding_note}
    Use these values in the environmental context section to explain the atmospheric setup.
    """

    # --- DAT surveyed tracks (tornado-only) ---
    dat_section = ""
    if report.dat_tracks and is_tornadic:
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

    # --- VAD wind profile (tornado-only: SRH framing leans on mesocyclone/tornado) ---
    vad_section = ""
    if report.vad_srh and is_tornadic:
        v = report.vad_srh
        vad_section = f"""
    VAD WIND PROFILE (extracted from first radar scan - event-time atmospheric profile):
    0-1km SRH: {v.get('srh_01km')} m2/s2
    - <150 = weak rotation potential, 150-300 = moderate, 300-500 = significant, >500 = extreme
    0-3km SRH: {v.get('srh_03km')} m2/s2
    - >150 = supercell favorable, >300 = significant tornado potential
    Note: VAD winds are derived from the radar velocity field at event time, more representative
    of the actual storm environment than the pre-event balloon sounding.
    Use these values alongside the sounding to describe the kinematic environment.
    If SRH values are high, discuss their role in supporting mesocyclone development and tornado potential.
    """

    # --- Computed totals ---
    total_deaths = sum(e.get("deaths_direct", 0) or 0 for e in report.ncei_events)
    total_injuries = sum(e.get("injuries_direct", 0) or 0 for e in report.ncei_events)

    tornado_events = [e for e in report.ncei_events if e.get("event_type") == "Tornado"]
    episodes = [e.get("episode_id") for e in tornado_events if e.get("episode_id")]
    dominant_episode = Counter(episodes).most_common(1)[0][0] if episodes else None

    episode_path_total = 0.0
    for e in report.ncei_events:
        if e.get("event_type") == "Tornado" and e.get("episode_id") == dominant_episode:
            try:
                episode_path_total += float(e.get("tor_length_mi") or 0)
            except (ValueError, TypeError):
                pass

    # Peak wind/hail across NCEI records (magnitude carries gust kt / hail inches)
    max_hail_in = None
    max_wind_kt = None
    for e in report.ncei_events:
        et = e.get("event_type")
        raw_mag = e.get("magnitude")
        try:
            mag = float(raw_mag) if raw_mag not in (None, "") else None
        except (ValueError, TypeError):
            mag = None
        if mag is None:
            continue
        if et in ("Hail", "Marine Hail"):
            max_hail_in = mag if max_hail_in is None else max(max_hail_in, mag)
        elif et in ("Thunderstorm Wind", "Marine Thunderstorm Wind"):
            max_wind_kt = mag if max_wind_kt is None else max(max_wind_kt, mag)

    computed_totals = f"""
    COMPUTED TOTALS (pre-summed across all NCEI county records, use these figures, do not re-sum):
    Total direct deaths: {total_deaths}
    Total direct injuries: {total_injuries}
    Total NCEI records: {len(report.ncei_events)}
    """

    if tornado_count > 0:
        computed_totals += f"""Total tornado records: {tornado_count}
    Total path length across all county segments of dominant tornado system: {round(episode_path_total, 1)} miles
    Note: NCEI records are split by county. Do not report a single county's path length as the total.
    Path lengths summed above represent all county segments of the same tornado system.
    """

    if max_hail_in is not None:
        computed_totals += (
            f"    Peak hail size (NCEI MAGNITUDE): {max_hail_in} inches\n"
        )
    if max_wind_kt is not None:
        computed_totals += f"    Peak thunderstorm wind gust (NCEI MAGNITUDE): {round(max_wind_kt)} kt\n"

    computed_totals += "    Note: Do not reference episode IDs, event IDs, or other internal database identifiers in the narrative.\n"

    # --- Nyquist / velocity pinning: detect in code, inject a note only when it is real ---
    radar_nyquist_note = ""
    vel_values = []
    for scan in report.radar_features or []:
        for key in ("max_inbound_kt", "max_outbound_kt", "max_inbound", "max_outbound"):
            val = scan.get(key)
            if val is not None:
                try:
                    vel_values.append(abs(float(val)))
                except (ValueError, TypeError):
                    pass

    if vel_values:
        peak = max(vel_values)
        pinned_scans = sum(1 for v in vel_values if abs(v - peak) < 0.5)
        if peak > 0 and pinned_scans >= 2:
            radar_nyquist_note = (
                f"\n    NOTE: Max velocity readings reached approximately {round(peak)} kt "
                f"on {pinned_scans} scans, consistent with the radar's effective measurement "
                f"limit (Nyquist velocity). State this at most once. Do not claim it proves "
                f"higher winds.\n"
            )

    radar_section = f"""
RADAR FEATURES (extracted from Level II volume scans):
Fields: timestamp, max reflectivity (dBZ), height of max reflectivity (kft AGL),
echo tops at 18/50 dBZ (kft), max inbound/outbound velocities (knots).
Interpret only the trends actually present in these scans. Possible signatures, to be
mentioned ONLY if this event's data shows them: updraft strengthening (rising echo tops),
hail core descent (reflectivity max intensifying as its height drops), and mesocyclone
rotation (a strong velocity couplet).
{radar_nyquist_note}{json.dumps(report.radar_features, indent=2, default=str)}
"""

    user_prompt = f"""Generate a post-event report narrative for the following event.

EVENT: {report.event_name}
DATE: {report.event_date}
LOCATION: {report.location}
EVENT CLASSIFICATION: {event_class} (dominant NCEI record type: {dominant_type})

Do not introduce tornado, supercell, mesocyclone, or EF-scale framing unless tornado
records appear below. Match the framing to the classification above. Omit sections that
have no supporting data rather than speculating to fill them.

--- AUTHORITATIVE SURVEY DATA ---

{dat_section}

{computed_totals}

NCEI STORM EVENTS ({len(report.ncei_events)} records - post-survey verified):
Prefer these over LSRs for ratings, dimensions, magnitudes, casualties, and damage estimates.
NCEI records are split by county. Use COMPUTED TOTALS and DAT for aggregate figures.
{json.dumps(report.ncei_events, indent=2, default=str)}

--- OBSERVATIONAL DATA ---

WARNINGS ISSUED ({len(report.warnings)} total):
{json.dumps(report.warnings, indent=2, default=str)}

LOCAL STORM REPORTS ({len(report.lsrs)} total):
{json.dumps(report.lsrs, indent=2, default=str)}

{radar_section}

--- ATMOSPHERIC CONTEXT ---

{sounding_section}

{vad_section}

{outbreak_section}

--- TIMING & WARNINGS ---

{lead_time_section}

Write the narrative HTML now. Cite specific timestamps and values. Only use data above."""

    raw = client.generate(system_prompt, user_prompt, max_tokens=8192)
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
    """Compute impact summary stats for the report template.

    Three decisions are kept SEPARATE here, because conflating them is what put a
    0.9 mi EFU path at the top of a derecho report and blanked its peak gust:

      1. Peak magnitudes (hail size, wind gust) are scanned across ALL records,
         regardless of which hazard "leads". A tornadic event can still have a
         headline-worthy gust.
      2. Impact attribution (which deaths/injuries/damage the summary reports, and
         the "tornadoes" vs "severe weather" label) follows whichever hazard caused
         the MOST harm, by deaths then damage. A token tornado in a wind-driven
         event does not seize the impact totals.
      3. Tornado EF rating and path length are computed from tornado records on
         PRESENCE. They render whenever a tornado occurred; their own template
         guards decide display. They do not depend on attribution.

    NOTE: the EF2 framing-lead bar (which hazard the narrative leads with) lives in
    generate_narrative, not here. This function only feeds the summary cards.
    """

    print(">>> compute_summary BUILD v2 running")

    # --- 1. Peak magnitudes across ALL records (independent of attribution) ---
    max_hail_in = None
    max_wind_kt = None
    for e in report.ncei_events:
        et = e.get("event_type")
        mag_raw = e.get("magnitude")
        try:
            mag = float(mag_raw) if mag_raw not in (None, "") else None
        except (ValueError, TypeError):
            mag = None
        if mag is None:
            continue
        if et in ("Hail", "Marine Hail"):
            if max_hail_in is None or mag > max_hail_in:
                max_hail_in = mag
        elif et in ("Thunderstorm Wind", "Marine Thunderstorm Wind"):
            if max_wind_kt is None or mag > max_wind_kt:
                max_wind_kt = mag

    # --- 2. Determine the dominant hazard by CONSEQUENCE, not by presence ---
    TORNADO_FAMILY = {"Tornado"}
    SEVERE_FAMILY = {
        "Thunderstorm Wind",
        "Marine Thunderstorm Wind",
        "High Wind",
        "Strong Wind",
        "Hail",
        "Marine Hail",
        "Lightning",
    }

    def _family_tally(types: set) -> tuple[int, float]:
        deaths = 0
        damage = 0.0
        for e in report.ncei_events:
            if e.get("event_type") in types:
                deaths += e.get("deaths_direct", 0) or 0
                if e.get("damage_property"):
                    damage += e["damage_property"]
        return deaths, damage

    tor_deaths, tor_damage = _family_tally(TORNADO_FAMILY)
    sev_deaths, sev_damage = _family_tally(SEVERE_FAMILY)
    has_tornado = any(e.get("event_type") == "Tornado" for e in report.ncei_events)
    has_convective = any(
        e.get("event_type") in SEVERE_FAMILY for e in report.ncei_events
    )
    is_bail = not has_tornado and not has_convective

    print(
        f">>> has_tornado={has_tornado} has_convective={has_convective} is_bail={is_bail}"
    )

    # Tornado leads the impact attribution only if it actually caused the most harm.
    # Deaths first, damage as tiebreak. Joplin -> tornado. Derecho -> severe weather.
    tornado_leads = has_tornado and (
        tor_deaths > sev_deaths
        or (tor_deaths == sev_deaths and tor_damage >= sev_damage)
    )

    if tornado_leads:
        impact_types = TORNADO_FAMILY
        impact_label = "tornadoes"
    elif is_bail:
        impact_types = None  # count every record
        impact_label = "all hazards"
    else:
        impact_types = SEVERE_FAMILY
        impact_label = "severe weather"

    print(f">>> label will be: {impact_label}")  # put after the if/elif/else

    # --- Impact totals, scoped to the dominant hazard family ---
    total_deaths = 0
    total_injuries = 0
    total_damage = 0.0
    for e in report.ncei_events:
        if impact_types is None or e.get("event_type") in impact_types:
            total_deaths += e.get("deaths_direct", 0) or 0
            total_injuries += e.get("injuries_direct", 0) or 0
            if e.get("damage_property"):
                total_damage += e["damage_property"]

    # --- 3. Tornado EF rating + path length, computed on PRESENCE ---
    # These come from tornado records regardless of whether the tornado leads impacts,
    # so a tornado in a wind-driven event still surfaces its rating/track.
    max_ef = None
    max_ef_rank = -1
    max_path = 0.0
    total_path = 0.0
    for e in report.ncei_events:
        if e.get("event_type") != "Tornado":
            continue

        raw_scale = e.get("tor_f_scale")
        if raw_scale:
            # Normalize legacy 'F' and modern 'EF' (e.g. "F5 "/"EF5" -> "5").
            # EFU/FU normalize to "U", which int() rejects, so unrated stays rank -1.
            clean_rating = str(raw_scale).strip().replace("EF", "").replace("F", "")
            try:
                rank = int(clean_rating)
                if rank > max_ef_rank:
                    max_ef_rank = rank
                    max_ef = str(raw_scale).strip()  # preserve "F5"/"EF5" for UI
            except (ValueError, TypeError):
                pass

        if e.get("tor_length_mi"):
            try:
                length = float(e["tor_length_mi"])
                total_path += length
                if length > max_path:
                    max_path = length
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

    # Suppress damage if clearly incomplete relative to event severity.
    if max_ef_rank >= 3 and total_damage < 100_000:
        total_damage = None
    elif total_damage < 10_000:
        total_damage = None

    # DAT path length takes priority over NCEI.
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
        "max_hail_in": max_hail_in,
        "max_wind_kt": round(max_wind_kt) if max_wind_kt is not None else None,
        "max_dbz": round(max(radar_dbz), 1) if radar_dbz else None,
        "max_tops": round(max(radar_tops), 1) if radar_tops else None,
        "warning_count": len(report.warnings),
        "lsr_count": len(report.lsrs),
        "outbreak_context": report.outbreak_context,
        "lead_time": report.lead_time,
        "is_bail": is_bail,
        "damage_may_overcount": is_bail,
    }
