"""Assembles the final HTML report from structured data + LLM narrative."""
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import markdown
from jinja2 import Environment, FileSystemLoader

from src.llm.base import get_client

TEMPLATES_DIR = Path(__file__).parent / "templates"
EF_RANK = {'EF0': 0, 'EF1': 1, 'EF2': 2, 'EF3': 3, 'EF4': 4, 'EF5': 5, 'EFU': -1}

@dataclass
class EventReport:
    """Bundled data for a single event — feeds both the LLM and the template."""
    event_name: str
    event_date: str
    location: str
    summary_metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    lsrs: list[dict[str, Any]] = field(default_factory=list)
    ncei_events: list[dict[str, Any]] = field(default_factory=list)
    radar_features: list[dict[str, Any]] = field(default_factory=list)
    radar_images: list[str] = field(default_factory=list)
    feature_notes: list[str] = field(default_factory=list)
    narrative: str = ""
    outbreak_context: dict | None = None


SYSTEM_PROMPT = """You are a meteorologist writing a post-event report for a severe weather event.

CRITICAL RULES:
1.  Only reference values, times, and facts provided in the structured data below.
2.  Do not invent radar values, casualty counts, or any other quantitative information.
3.  Do not interpret radar imagery - use only the pre-extracted numeric features.
4.  Cite warnings and LSRs by their timestamps when relevant.
5.  Write in clear, professional prose suitable for a public-facing report.
6.  If the data is incomplete or unclear, say so rather than speculating.
7.  SYNTHESIZE radar data - describe TRENDS (intensification, weakening, core descent)
    rather than listing every scan's values. The radar table is rendered separately
    in the report; your job is to interpret it, not transcribe it.
8.  Ignore notes about artifacts, capped values, or 'no field' - these are diagnostic
    metadata, not findings to report.
9.  Output is HTML, not markdown. Use <strong>, <em>, <ul>, <li> tags. Do not use **, *, or # for formatting.

Your output should be coherent narrative HTML (paragraphs, lists where appropriate),
not a flat data dump. Structure it as: overview, environmental context (if provided),
storm evolution timeline, warnings issued, impacts/LSRs, conclusion.
"""


def generate_narrative(report: EventReport) -> str:
    """Call the LLM to generate the narrative section."""
    client = get_client()

    user_prompt = f"""Generate a post-event report narrative for the following event.

EVENT: {report.event_name}
DATE: {report.event_date}
LOCATION: {report.location}

METADATA:
{json.dumps(report.summary_metadata, indent=2, default=str)}

WARNINGS ISSUED ({len(report.warnings)} total):
{json.dumps(report.warnings, indent=2, default=str)}

LOCAL STORM REPORTS ({len(report.lsrs)} total):
{json.dumps(report.lsrs, indent=2, default=str)}

NCEI STORM EVENTS ({len(report.ncei_events)} records - post-survey verified data):
These are authoritative NWS post-storm survey records. When available, prefer these
over LSR data for EF ratings, path dimensions, casualties, and damage estimates.

{json.dumps(report.ncei_events, indent=2, default=str)}

RADAR FEATURES (extracted numerically from Level II volume scans):
Each entry is one volume scan. Fields: max reflectivity (dBZ), height of max
reflectivity (thousands of feet AGL), echo tops at 18 dBZ and 50 dBZ thresholds
(thousands of feet), max inbound/outbound velocities (knots).

{json.dumps(report.radar_features, indent=2, default=str)}

Write the narrative HTML now. Use the radar features to describe storm
evolution - e.g., updraft strengthening (rising echo tops), high-reflectivity
core descent (suggests large hail), strong velocity couplets (suggests rotation).
Cite specific timestamps and values. Remember: only use the data above."""

    
    raw = client.generate(SYSTEM_PROMPT, user_prompt, max_tokens=8192)
    
    # LLM may emit markdown; convert to HTML for clean rendering
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
        

def build_html_string(report: EventReport, local_timezone: str = "UTC") -> str:
    """Render the report to an HTML string without writing to disk."""
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True)
    env.filters["fmt_time"] = lambda s: _format_time_with_local_tz(s, local_timezone)
    template = env.get_template("report.html")
    return template.render(report=report, summary=compute_summary(report))

def compute_summary(report: EventReport) -> dict:
    """Compute impact summary stats for the report template."""
    max_ef = None
    max_ef_rank = -1
    total_deaths = 0
    total_injuries = 0
    total_damage = 0.0
    max_path = 0.0

    for e in report.ncei_events:
        if e.get('deaths_direct'):
            total_deaths += e['deaths_direct']
        if e.get('injuries_direct'):
            total_injuries += e['injuries_direct']
        if e.get('damage_property'):
            total_damage += e['damage_property']
        if e.get('tor_length_mi'):
            try:
                length = float(e['tor_length_mi'])
                if length > max_path:
                    max_path = length
            except (ValueError, TypeError):
                pass
        if e.get('tor_f_scale'):
            rank = EF_RANK.get(e['tor_f_scale'], -1)
            if rank > max_ef_rank:
                max_ef_rank = rank
                max_ef = e['tor_f_scale']

    radar_dbz = [
        f['max_reflectivity_dbz'] for f in report.radar_features
        if f.get('max_reflectivity_dbz') is not None
    ]
    radar_tops = [
        f['echo_top_18dbz_kft'] for f in report.radar_features
        if f.get('echo_top_18dbz_kft') is not None
    ]
    
    # Suppress damage if clearly incomplete relative to event severity
    if max_ef_rank >= 3 and total_damage < 100_000:
        total_damage = None
    elif total_damage < 10_000:
        total_damage = None

    return {
        'max_ef': max_ef,
        'total_deaths': total_deaths,
        'total_injuries': total_injuries,
        'total_damage': total_damage,
        'max_path_mi': round(max_path, 1) if max_path > 0 else None,
        'max_dbz': round(max(radar_dbz), 1) if radar_dbz else None,
        'max_tops': round(max(radar_tops), 1) if radar_tops else None,
        'warning_count': len(report.warnings),
        'lsr_count': len(report.lsrs),
        'outbreak_context': report.outbreak_context,
    }