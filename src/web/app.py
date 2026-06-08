"""FastAPI app for the Severe Weather Event Reporter."""
from datetime import datetime, timedelta, date as date_type
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request, Response, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.data_sources import geocoding
from src.pipeline import run_pipeline
from src.report.builder import EventReport, build_html_string

from src import config

#from weasyprint import HTML as WeasyHTML

import json
import math

app = FastAPI(title="Severe Weather Event Reporter")

_jobs: dict[str, dict] = {}
# Structure: {"status": "processing"|"done"|"failed", "message": str, "error": str|None}

NEXRAD_START = date_type(1991, 6, 1)

WEB_STATIC_DIR = Path(__file__).parent / "static"
TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(WEB_STATIC_DIR)), name="static")

class ReportRequest(BaseModel):
    """Minimum input required to generate a report via the API."""
    event_name: str
    location: str
    lat: float | None = None     # if provided, skip backend geocoding
    lon: float | None = None
    date: str
    start_time: str | None = None
    end_time: str | None = None
    tz_mode: str = "local"
    zoom_km: float = 50.0 # standard
    max_radar_scans: int = 8
    lsr_search_km: float = 75.0


def _run_pipeline_job(
    slug: str,
    event_name: str,
    event_date: str,
    location: str,
    start_utc: datetime,
    end_utc: datetime,
    lat: float,
    lon: float,
    tz_name: str,
    zoom_km: float = 50.0,
    max_radar_scans: int = 8,
    lsr_search_km: float = 75.0,
)-> None:
    """Wrapper that runs the pipeline and updates job state"""
    
    def progress(msg: str) -> None:
        _jobs[slug]["message"] = msg
        
    # Set estimated time as first message before pipeline starts
    _jobs[slug]["message"] = f"Starting pipeline..."
    _jobs[slug]["estimated_seconds"] = (max_radar_scans * 22) + 60
    
    try:
        run_pipeline(
            event_name=event_name,
            event_date=event_date,
            location=location,
            radar_site=None,
            start=start_utc,
            end=end_utc,
            vtec_events=None,
            auto_discover=True,
            zoom_lat=lat,
            zoom_lon=lon,
            local_timezone=tz_name,
            zoom_km=zoom_km,
            max_radar_scans=max_radar_scans,
            lsr_search_km=lsr_search_km,
            progress=progress
        )
        _jobs[slug] = {"status": "done", "message" : "Complete", "error": None}
    except Exception as e:
        _jobs[slug] = {"status": "failed", "message" : "Failed", "error": str(e)}
        

@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/reports")
async def create_report(req: ReportRequest, background_tasks: BackgroundTasks):
    """Generate a new report from minimum input."""
    try:
        event_date = datetime.strptime(req.date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, f"Invalid date format: {req.date} (expected YYYY-MM-DD)")
    if event_date < NEXRAD_START:
        raise HTTPException(400, f"Events before {NEXRAD_START} are not supported. NEXRAD archive begins June 1991.")

    start_str = req.start_time or "18:00"
    end_str = req.end_time or "06:00"
    try:
        start_time = datetime.strptime(start_str, "%H:%M").time()
        end_time = datetime.strptime(end_str, "%H:%M").time()
    except ValueError:
        raise HTTPException(400, f"Invalid time format (expected HH:MM, got {start_str}/{end_str})")

    # Geocode (needed for coords + radar lookup either way)
    # Use provided coords if available; otherwise geocode the location string
    if req.lat is not None and req.lon is not None:
        lat, lon = req.lat, req.lon
    else:
        geo = geocoding.geocode(req.location)
        if geo is None:
            raise HTTPException(400, f"Could not geocode location: {req.location}")
        lat, lon = geo.lat, geo.lon

    tz_name = geocoding.get_timezone(lat, lon)

    # Build UTC window based on tz_mode
    if req.tz_mode == "utc":
        utc = ZoneInfo("UTC")
        start_utc = datetime.combine(event_date, start_time, tzinfo=utc)
        end_utc = datetime.combine(event_date, end_time, tzinfo=utc)
    else:
        local_tz = ZoneInfo(tz_name)
        start_local = datetime.combine(event_date, start_time, tzinfo=local_tz)
        end_local = datetime.combine(event_date, end_time, tzinfo=local_tz)
        start_utc = start_local.astimezone(ZoneInfo("UTC"))
        end_utc = end_local.astimezone(ZoneInfo("UTC"))

    if end_utc <= start_utc:
        end_utc += timedelta(days=1)

    slug = req.event_name.lower().replace(" ", "_")
    
    # If already done, just redirect
    if (config.OUTPUT_DIR / slug / "report_data.json").exists():
        return RedirectResponse(url=f"/reports/{slug}/", status_code=303)
    
        # If already running, redirect to status page
    if _jobs.get(slug, {}).get("status") == "processing":
        return RedirectResponse(url=f"/reports/{slug}/status", status_code=303)

    # Only one pipeline at a time
    if any(j.get("status") == "processing" for j in _jobs.values()):
        raise HTTPException(429, "Another report is currently generating. Please wait a few minutes and try again.")

    _jobs[slug] = {
        "status": "processing",
        "message": "Starting pipeline...",
        "estimated_seconds": (req.max_radar_scans * 22) + 60,
        "error": None,
    }

    background_tasks.add_task(
        _run_pipeline_job,
        slug=slug,
        event_name=req.event_name,
        event_date=event_date.strftime("%B %d, %Y"),
        location=req.location,
        start_utc=start_utc,
        end_utc=end_utc,
        lat=lat,
        lon=lon,
        tz_name=tz_name,
        zoom_km=req.zoom_km,
        max_radar_scans=req.max_radar_scans,
        lsr_search_km=req.lsr_search_km
    )

    return RedirectResponse(url=f"/reports/{slug}/status", status_code=303)


@app.get("/gallery")
async def gallery(request: Request):
    reports = []
    if config.OUTPUT_DIR.exists():
        for manifest_path in sorted(
            config.OUTPUT_DIR.glob("*/manifest.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            try:
                data = json.loads(manifest_path.read_text())

                # Enrich with summary data from report_data.json
                report_data_path = manifest_path.parent / "report_data.json"
                if report_data_path.exists():
                    rdata = json.loads(report_data_path.read_text())
                    ncei = rdata.get("ncei_events", [])
                    radar = rdata.get("radar_features", [])

                    # Max EF rating
                    ef_rank = {'EF0':0,'EF1':1,'EF2':2,'EF3':3,'EF4':4,'EF5':5,'EFU':-1}
                    max_ef = None
                    max_rank = -1
                    for e in ncei:
                        r = ef_rank.get(e.get('tor_f_scale',''), -1)
                        if r > max_rank:
                            max_rank = r
                            max_ef = e.get('tor_f_scale')

                    # Total deaths
                    total_deaths = sum(e.get('deaths_direct', 0) or 0 for e in ncei)

                    # Max dBZ
                    dbz_vals = [f['max_reflectivity_dbz'] for f in radar if f.get('max_reflectivity_dbz')]
                    max_dbz = round(max(dbz_vals), 1) if dbz_vals else None

                    data['max_ef'] = max_ef
                    data['total_deaths'] = total_deaths
                    data['max_dbz'] = max_dbz

                reports.append(data)
            except (json.JSONDecodeError, OSError):
                continue
    return templates.TemplateResponse(request, "gallery.html", {"reports": reports})


@app.get("/reports/{slug}/")
async def view_report(slug: str):
    data_path = config.OUTPUT_DIR / slug / "report_data.json"
    if not data_path.exists():
        raise HTTPException(404, f"Report not found: {slug}")
    
    try:
        data = json.loads(data_path.read_text())
        report = EventReport(
        event_name=data["event_name"],
        event_date=data["event_date"],
        location=data["location"],
        narrative=data.get("narrative", ""),
        warnings=data.get("warnings", []),
        lsrs=data.get("lsrs", []),
        ncei_events=data.get("ncei_events", []),
        sounding_indices=data.get("sounding_indices", {}),
        sounding_image=data.get("sounding_image", None),
        outbreak_context=data.get("outbreak_context", None),
        lead_time=data.get("lead_time", None),
        radar_features=data.get("radar_features", []),
        radar_images=data.get("radar_images", []),
        feature_notes=data.get("feature_notes", []),
    )

        html = build_html_string(report, local_timezone=data.get("local_timezone", "UTC"))
        return HTMLResponse(html)
    except (json.JSONDecodeError, KeyError) as e:
        raise HTTPException(500, f"Error loading report data: {e}")
    
    
@app.get("/reports/{slug}/images/{filename}")
async def report_image(slug: str, filename: str):
    img_path = config.OUTPUT_DIR / slug / "images" / filename
    if not img_path.exists():
        raise HTTPException(404)
    return FileResponse(img_path)


@app.get("/reports/{slug}/status")
async def report_status_page(slug: str, request: Request):
    """Renders the status polling page."""
    # If report already exists (e.g. cached), redirect immediately
    if (config.OUTPUT_DIR / slug / "report_data.json").exists():
        return RedirectResponse(url=f"/reports/{slug}/", status_code=303)
    return templates.TemplateResponse(request, "status.html", {"slug": slug})


@app.get("/reports/{slug}/status.json")
async def report_status_json(slug: str):
    if (config.OUTPUT_DIR / slug / "report_data.json").exists():
        return {"status": "done", "message": "Complete"}
    job = _jobs.get(slug)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job