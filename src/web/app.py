"""FastAPI app for the Severe Weather Event Reporter."""
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from src.data_sources import geocoding
from src.pipeline import run_pipeline

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from src import config

app = FastAPI(title="Severe Weather Event Reporter")

# Serve the output directory under /reports
# This makes /reports/<slug>/static/* and /reports/<slug>/images/* work for free.
WEB_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/reports", StaticFiles(directory=str(config.OUTPUT_DIR), html=True), name="reports")
app.mount("/static", StaticFiles(directory=str(WEB_STATIC_DIR)), name="static")

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


class ReportRequest(BaseModel):
    event_name: str
    location: str
    lat: float | None = None     # if provided, skip backend geocoding
    lon: float | None = None
    date: str
    start_time: str | None = None
    end_time: str | None = None
    tz_mode: str = "local"


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/reports")
async def create_report(req: ReportRequest):
    """Generate a new report from minimum input."""
    try:
        event_date = datetime.strptime(req.date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, f"Invalid date format: {req.date} (expected YYYY-MM-DD)")

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

    try:
        run_pipeline(
            event_name=req.event_name,
            event_date=event_date.strftime("%B %d, %Y"),
            location=req.location,
            radar_site=None,  # type: ignore
            start=start_utc,
            end=end_utc,
            vtec_events=None,
            auto_discover=True,
            zoom_lat=lat,
            zoom_lon=lon,
            local_timezone=tz_name,
        )
    except Exception as e:
        raise HTTPException(500, f"Pipeline failed: {e}")

    return RedirectResponse(url=f"/reports/{slug}/", status_code=303)