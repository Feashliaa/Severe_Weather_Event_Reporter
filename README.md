# Severe Weather Event Reporter

An AI-powered post-event severe weather report generator. Input a location, date, and time window - the tool fetches NWS warnings, Local Storm Reports (IEM), NCEI Storm Events survey data, and archived NEXRAD Level II radar, then produces a polished HTML report with an animated radar loop, interactive event map, volume scan analysis table, and a structured LLM narrative grounded entirely in real meteorological data.

The kind of breakdown that normally takes a meteorologist hours to assemble manually - I've got something automated down to running in about 3-5 minutes.

## The Problem

Major events get coverage. EF4-5s, significant outbreaks, high-fatality events - NWS publishes service assessments, media writes them up, researchers document them. But the long tail doesn't. EF1-EF3s in rural counties, isolated hail events, derecho segments, marginally tornadic nights - they get a database entry and maybe a tweet. Weeks later, nobody can answer "what actually happened Tuesday night in [county]" without spending an hour piecing together IEM, NCEI, and S3 radar files manually.

This tool can potentially fill that gap.

## What It Produces

Each report includes:

- **AI narrative** - structured overview, storm evolution, warnings issued, impacts and conclusion. Written from extracted numeric radar features and structured warning/LSR/NCEI data, not hallucinated from images
- **Animated radar loop** - reflectivity + velocity (+ correlation coefficient + spectrum width for 2013+ events), dark map background, county lines, city labels, event location marked
- **Volume scan analysis table** - max dBZ, echo tops, velocity couplet, timestamped for each processed scan
- **Interactive event map** - Leaflet map with warning polygons, LSR points, and NCEI tornado tracks color-coded by EF rating
- **Active warnings section** - all VTEC warnings with issued/expired times, polygon coverage, forecaster
- **Local storm reports** - deduplicated, polygon-filtered, color-coded by type
- **NCEI Storm Survey data** - post-survey verified EF rating, path length/width, fatalities, injuries, property damage, full NWS event narrative

## Stack

| Layer | Choice |
|---|---|
| Backend | Python + FastAPI |
| Frontend | Vanilla JS + Tailwind CSS + Jinja2 |
| Radar processing | Py-ART + matplotlib + Cartopy |
| Maps | Leaflet + CartoDB dark tiles |
| LLM | Model-agnostic (Claude, Gemini, GPT) - BYOK |
| Geocoding | OSM Nominatim + timezonefinder |

## Data Sources

| Source | What it provides |
|---|---|
| NOAA NEXRAD on AWS S3 (`noaa-nexrad-level2`) | Level II radar archive, 1991–present |
| IEM Mesonet | VTEC warning archive, storm-based warning polygons, LSRs |
| NCEI Storm Events Database | Post-survey EF ratings, path dimensions, casualties, damage estimates |
| OSM Nominatim | Geocoding + location autocomplete |

## Feature Availability by Date

The pipeline gates features based on historical product availability and per-radar commissioning dates:

| Feature | Available from |
|---|---|
| NEXRAD Level II radar | Per-radar (most 1993-1996, all CONUS by 1997) |
| IEM LSR archives | ~2002 |
| VTEC warning system | January 1996 |
| Storm-based warning polygons | October 2007 |
| Dual-polarization (CC, spectrum width) | March 2013 |

Pre-cutoff events generate reports with available data and a notice explaining what's missing.

## Getting Started

```bash
git clone https://github.com/Feashliaa/Severe_Weather_Event_Reporter
cd Severe_Weather_Event_Reporter

python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS

pip install -r requirements.txt
cp .env.example .env
# Add your LLM API key to .env (ANTHROPIC_API_KEY, OPENAI_API_KEY, or GEMINI_API_KEY)

uvicorn src.web.app:app --reload --port 8000
```

Open `http://localhost:8000`, enter an event name, location, and date range.

Reports take about 3-5 minutes to generate (radar rendering is the bottleneck). Progress is shown live on the status page.

## Project Structure

```
severe_weather_event_reporter/
├── src/
│   ├── config.py                   # env vars, paths
│   ├── event_config.py             # Event configuration loader
│   ├── models.py                   # shared dataclasses
│   ├── pipeline.py                 # main orchestrator
│   ├── feature_gates.py            # date-based product availability
│   │
│   ├── data_sources/
│   │   ├── discovery.py            # auto-discover warnings at a point
│   │   ├── geocoding.py            # Nominatim + timezone + state/county parsing
│   │   ├── iem.py                  # IEM warnings + LSRs
│   │   ├── ncei.py                 # NCEI Storm Events CSV client
│   │   ├── nexrad_stations.json    # JSON of nexrad stations
│   │   ├── nexrad.py               # NEXRAD Level II from S3
│   │   └── radar_locator.py        # nearest NEXRAD lookup
│   │
│   ├── llm/
│   │   ├── base.py                 # Base runner/loader
│   │   ├── anthropic_client.py     # anthropic setup
│   │   ├── gemini_client.py        # gemini setup
│   │   └── openai_client.py        # openai setup
│   │
│   ├── radar/
│   │   ├── processor.py            # Py-ART feature extraction
│   │   └── renderer.py             # Cartopy quad-panel rendering
│   │
│   ├── report/
│   │   └── builder.py              # EventReport dataclass + LLM prompt
│   │   ├── templates/              # report.html
│   │   └── static/                 # radar-loop.js, report.css, favicon.svg
│   │
│   └── web/
│       ├── app.py                  # FastAPI routes + background jobs
│       ├── templates/              # index.html, gallery.html, status.html
│       └── static/                 # form.js, event-map.js, radar-loop.js, report.css, favicon.svg
│
├── scripts/
│   ├── run_event.py                # CLI runner for JSON event configs
│   └── download_ncei.py           # bulk NCEI CSV downloader
│
├── events/                         # JSON event configs (for CLI use)
├── .cache/                         # downloaded radar + NCEI CSVs (gitignored)
└── output/                         # generated reports (gitignored)
```

## Tested Events

| Event | Date | Notes |
|---|---|---|
| Joplin, MO tornado | May 22, 2011 | EF5, 161 fatalities |
| Moore, OK tornado | May 20, 2013 | EF5, first dual-pol test |
| Hackleburg, AL tornado | April 27, 2011 | Part of 2011 Super Outbreak |
| Greenfield, IA tornado | May 21, 2024 | EF4, 4 fatalities |
| Mayfield, KY tornado | December 10, 2021 | EF4, night event |
| Rolling Fork, MS tornado | March 24, 2023 | EF4 |
| Enderlin, ND tornado | June 20, 2025 | EF5, train derailment |

## Roadmap

**Done:**
- End-to-end pipeline (warnings -> LSRs -> NCEI -> radar -> LLM -> report)
- Auto-discovery of warnings via 9-point grid search
- S3 radar fallback loop (tries nearest radar, falls back if no archive data)
- Feature gating with graceful degradation for historical events
- Dual-polarization radar panels (CC + spectrum width) for 2013+ events
- Cartopy map overlay with dark background, county/state lines, city labels
- Background jobs with real-time progress updates
- Report caching + gallery
- NCEI Storm Events integration
- Interactive Leaflet event map with warning polygons + LSR points + tornado tracks
- Concurrent pipeline prevention

**Planned:**
- Dockerize + Railway deployment
- PostgreSQL for report storage + NCEI data (replaces flat files)
- Celery + Redis job queue (replaces BackgroundTasks)
- Cloudflare R2 for radar image storage
- Multi-radar support for long-track events
- SPC tornado path polygons overlay
- Impact summary card
- Storm-relative velocity (SRV) product

## Notes

- Reports are generated on-demand and cached. Re-requesting the same event name redirects to the existing report instantly.
- Only one pipeline runs at a time. Concurrent submissions return a 429 with a retry message.
- NEXRAD archive starts June 1991. Events before that date are not supported.
- NCEI Storm Events data lags 4-8 weeks for recent events. The pipeline falls back to LSR data when NCEI has no record yet.
- LLM API keys are user-supplied (BYOK). The tool is model-agnostic - Claude, GPT-5, and Gemini are all supported.

## License

TBD


## Resources

**Primary data sources:**
- NEXRAD Level II radar: [noaa-nexrad-level2](https://noaa-nexrad-level2.s3.amazonaws.com)
- IEM Mesonet (warnings + LSRs): [mesonet.agron.iastate.edu](https://mesonet.agron.iastate.edu)
- NCEI Storm Events CSV: [ncei.noaa.gov](https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/)
- OSM Nominatim (geocoding): [nominatim.openstreetmap.org](https://nominatim.openstreetmap.org)

**Reference/lookup:**
- NOAA Billion-Dollar Disasters (discontinued): [ncei.noaa.gov](https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.nodc:0209268)
- SPC severe weather GIS: [spc.noaa.gov](https://www.spc.noaa.gov/gis/svrgis/)

**Frontend libraries:**
- Leaflet: [leafletjs.com](https://leafletjs.com)
- CartoDB basemaps: [carto.com](https://carto.com/basemaps)
- Tailwind CSS: [tailwindcss.com](https://tailwindcss.com)
- Oswald font: [fonts.google.com](https://fonts.google.com/specimen/Oswald)

**NWS/NOAA institutional:**
- NEXRAD network info: [roc.noaa.gov](https://www.roc.noaa.gov/WSR88D/)
- IEM VTEC archive: [mesonet.agron.iastate.edu/vtec](https://mesonet.agron.iastate.edu/vtec/)