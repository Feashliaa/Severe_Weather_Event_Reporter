# Severe Weather Event Reporter

An AI-powered tool that auto-generates polished post-event reports for severe weather events. Pulls NWS warnings, Local Storm Reports, and archived NEXRAD Level II radar data, then assembles a structured timeline with embedded radar imagery, an animated radar loop, and a coherent written narrative - the kind of post-event breakdown that normally takes hours to produce manually.

## Problem

Major events (EF4/EF5s, big outbreaks) get manual writeups from NWS, media, etc within days. But the long tail - EF1-EF3s in rural areas, marine waterspouts, hail events, derecho segments - mostly get a tweet and a database entry. This tool fills that gap, plus serves researchers, insurance, emergency managers, and weather enthusiasts who want to understand "what happened last Tuesday in [random county]" without piecing it together manually.

## Differentiators

Versus existing tools (IEM Raccoon, NCEI Storm Events, NWS service assessments):

- **Polished, shareable output** - modern web report with retro TWC/weather.gov aesthetic
- **AI narrative grounded in structured data** - the LLM writes from extracted numeric features (max reflectivity, echo tops, velocity couplets, warning timing), not by hallucinating from images
- **Speed** - minutes, not hours or months
- **Self-serve** - anyone can generate a report on any event in the archive via a simple web form
- **Auto-discovery** - supply just a location and date; the tool finds the warnings, picks the right radar, and pulls the LSRs automatically

## Current Status

**Working:**
- End-to-end pipeline from event input to rendered HTML report
- Web form with autocomplete location search and timezone-aware time input
- Auto-discovery of NWS warnings from a date/location
- Geocoding via OSM Nominatim + automatic nearest-NEXRAD lookup
- Dual-panel reflectivity + velocity radar rendering via Py-ART
- Animated radar loop with playback controls
- Comprehensive LSR fetch with deduplication and priority sorting
- LLM narrative grounded in extracted radar features and structured data
- Feature gating based on event date (graceful degradation for pre-NEXRAD or pre-dual-pol events)

**Tested on:** Joplin (2011), Moore (2013), Mayfield (2021), Greenfield (2024), and a recent live event (Bogue Chitto May 2026)

## Stack

| Layer | Choice |
|---|---|
| Backend | Python + FastAPI |
| Frontend | Vanilla JS + Tailwind CSS + Jinja2 templates |
| Geo/Map | Leaflet + leaflet-geosearch (OSM Nominatim) |
| Radar | Py-ART + matplotlib |
| LLM | Model-agnostic (Claude, GPT, Gemini) - BYOK |

### Key Python Libraries

- `fastapi` + `uvicorn` - web framework
- `pyart` - NEXRAD Level II radar processing
- `httpx` - HTTP client for data source APIs
- `pydantic` - request validation
- `geopy` + `timezonefinder` - geocoding and timezone resolution
- `markdown` - converts LLM markdown output to HTML
- `jinja2` - HTML templating
- `anthropic`, `openai`, `google-genai` - LLM clients

## Data Sources

| Source | Purpose | Access |
|---|---|---|
| NOAA NEXRAD on AWS S3 | Level II radar files | `s3://noaa-nexrad-level2/` |
| IEM Mesonet | Historical warnings (VTEC), LSRs, polygon archives | `mesonet.agron.iastate.edu` |
| OSM Nominatim | Geocoding + autocomplete | `nominatim.openstreetmap.org` |
| NCEI Storm Events DB | Authoritative post-survey records | Planned for Phase 2 |

## Feature Availability by Date

The pipeline gates features based on when products became available:

- **NEXRAD Level II radar** - per-radar commissioning dates (1991-2014, mostly 1993-1996)
- **Storm-Based Warning polygons** - October 2007
- **Dual-polarization radar (CC, ZDR)** - March 2013
- **Reliable IEM LSR archives** - 2002

Pre-cutoff events generate reports with the available data and a notice explaining what's missing.

## Project Structure

```
severe_weather_event_reporter/
├── README.md
├── .gitignore
├── .env.example
├── requirements.txt
├── docker-compose.yml          # v2+
├── Dockerfile                  # v2+
│
├── src/
│   ├── config.py               # env vars, settings
|   ├── models.py               # shared dataclasses (VTECEventRef)
│   ├── pipeline.py             # main orchestrator
│   ├── event_config.py         # JSON-defined event configs
│   ├── feature_gates.py        # date-based product availability
│   │
│   ├── data_sources/           # API clients for external data
│   │   ├── geocoding.py        # OSM Nominatim + timezone
│   │   ├── radar_locator.py    # nearest NEXRAD lookup
│   │   ├── iem.py              # IEM archive
│   │   ├── nexrad.py           # NEXRAD Level II from S3
│   │   └── discovery.py        # auto-discover warnings at a point
│   │
│   ├── radar/                  # radar processing
│   │   ├── processor.py        # Py-ART wrapper, feature extraction
│   │   └── renderer.py         # image rendering
│   │
│   ├── llm/                    # LLM adapters
│   │   ├── base.py             # abstract interface
│   │   ├── anthropic_client.py
│   │   ├── openai_client.py
│   │   └── gemini_client.py
│   │
│   ├── report/                 # report assembly
│   │   ├── builder.py          # orchestrates data > narrative > HTML
│   │   ├── static/         
│   │   └── templates/
│   │       └── report.html
│   │  
│   └── web/                    # FastAPI app
│       ├── app.py
│       ├── templates/index.html
│       └── static/form.js
│
├── scripts/
│   ├── run_event.py            # CLI runner - loads events/*.json
│   ├── build_nexrad_stations.py
│   └── add_commission_dates.py
│
├── events/                     # event config JSON (legacy, kept for tests)
│   ├── joplin_2011.json
│   ├── moore_2013.json
│   └── mayfield_2021.json
│
└── output/                     # generated reports (gitignored)
```
## Getting Started

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Add your Gemini/Anthropic/OpenAI API key to .env

uvicorn src.web.app:app --reload --port 8000
# Open http://localhost:8000 in a browser
```

Or run a preconfigured event from the command line:

```bash
python -m scripts.run_event joplin_2011
```

## Roadmap

### Phase 1 - Date-based feature gating | Done
### Phase 2 - Print/PDF export (in progress)
### Phase 3 - Cartopy map overlay on radar (county lines, town labels)
### Phase 4 - NCEI Storm Events DB integration (authoritative post-survey records)
### Phase 5 - Multi-radar support for long-track events
### Phase 6 - Storm-relative velocity (SRV) product
### Phase 7 - Dual-pol products (CC, ZDR) for 2013+ events

### Eventually
- Persistence (Postgres) + report history
- Auth + BYOK API keys
- Public gallery of pre-rendered demo events
- Click-on-map location selection (Leaflet map UI)

## License

TBD