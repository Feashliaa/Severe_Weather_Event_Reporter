# Severe Weather Event Reporter

An AI-powered post-event severe weather report generator. Input a location, date, and time window - the tool fetches NWS warnings, Local Storm Reports (IEM), NCEI Storm Events survey data, pre-event upper air soundings, NWS Damage Assessment Toolkit survey geometry, SPC convective outlooks, and archived NEXRAD Level II radar, then produces a polished HTML report with an animated radar loop, interactive event map, Skew-T/hodograph sounding analysis, volume scan analysis table, and a structured LLM narrative grounded entirely in real meteorological data.

The kind of breakdown that normally takes a meteorologist hours to assemble manually - automated down to about 2 minutes.

## The Problem

Major events get coverage. EF4-5s, significant outbreaks, high-fatality events - NWS publishes service assessments, media writes them up, researchers document them. But the long tail doesn't. EF1-EF3s in rural counties, isolated hail events, derecho segments, marginally tornadic nights - they get a database entry and maybe a tweet. Weeks later, nobody can answer "what actually happened Tuesday night in [county]" without spending an hour piecing together IEM, NCEI, and S3 radar files manually.

This tool fills that gap.

## What It Produces

Each report includes:

- **Impact summary card** - EF rating, fatalities, injuries, warning lead time, path length (DAT surveyed or NCEI), max reflectivity, echo tops, warning count, LSR count, and property damage at a glance
- **Warning lead time analysis** - minutes between the first tornado warning and the highest-rated tornado's touchdown, computed from VTEC issuance times and NCEI survey times, with data-quality flagging when sources conflict
- **Outbreak context** - if the event falls within a NOAA Billion-Dollar Disaster, the report surfaces the outbreak name, total economic damage, and total deaths (1980-2024 archive)
- **Pre-event sounding + VAD hodograph** - Skew-T log-P diagram with parcel path and CAPE/CIN shading alongside a VAD hodograph derived from event-time radar velocity data, plus computed indices (CAPE, CIN, 0-6km bulk shear, 0-1km SRH, 0-3km SRH, surface conditions)
- **AI narrative** - structured overview, environmental context, storm evolution, warnings issued, and impacts. Written from extracted numeric radar features and structured warning/LSR/NCEI/sounding/DAT data, not hallucinated from images
- **Animated radar loop** - reflectivity + velocity (+ correlation coefficient + spectrum width for 2013+ events), dark map background, county lines, city labels, event location marked
- **Volume scan analysis table** - max dBZ, echo tops, velocity couplet, timestamped for each processed scan
- **Interactive event map** - Leaflet map with SPC Day 1 convective outlook overlay, NWS DAT surveyed tornado tracks (with EF-comment override), warning polygons, LSR points, and NCEI straight-line fallback for unsurveyed events
- **Active warnings section** - all VTEC warnings with issued/expired times, polygon coverage, forecaster
- **Local storm reports** - deduplicated, polygon-filtered, color-coded by type
- **NCEI Storm Survey data** - post-survey verified EF rating, path length/width, fatalities, injuries, property damage, full NWS event narrative

## Stack

| Layer             | Choice                               |
| ----------------- | ------------------------------------ |
| Backend           | Python + FastAPI                     |
| Frontend          | Vanilla JS + Tailwind CSS + Jinja2   |
| Radar processing  | Py-ART + matplotlib + Cartopy        |
| Sounding analysis | MetPy + siphon                       |
| Maps              | Leaflet + CartoDB dark tiles         |
| LLM               | Model-agnostic (Claude, Gemini, GPT) |
| Geocoding         | OSM Nominatim + timezonefinder       |

## Data Sources

| Source                                       | What it provides                                                                                 |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| NOAA NEXRAD on AWS S3 (`noaa-nexrad-level2`) | Level II radar archive, 1991-present                                                             |
| IEM Mesonet                                  | VTEC warning archive, storm-based warning polygons, LSRs, RAOB soundings                         |
| University of Wyoming Upper Air Archive      | Sounding fallback when IEM archive has gaps (via siphon)                                         |
| NCEI Storm Events Database                   | Post-survey EF ratings, path dimensions, casualties, damage estimates, episode IDs               |
| NWS Damage Assessment Toolkit (DAT)          | Surveyed tornado track geometry - real curved multi-point paths, variable-width damage corridors |
| SPC Convective Outlook Archive               | Day 1 categorical outlook overlay - GeoJSON (2020+), shapefile fallback (2003+)                  |
| NOAA Billion-Dollar Disasters                | Outbreak-level economic damage and death tolls, 1980-2024 (discontinued, archived locally)       |
| OSM Nominatim                                | Geocoding + location autocomplete                                                                |

## Feature Availability by Date

The pipeline gates features based on historical product availability and per-radar commissioning dates:

| Feature                                | Available from                                           |
| -------------------------------------- | -------------------------------------------------------- |
| NEXRAD Level II radar                  | Per-radar (most 1993-1996, all CONUS by 1997)            |
| IEM LSR archives                       | ~2002                                                    |
| VTEC warning system                    | January 1996                                             |
| Storm-based warning polygons           | October 2007                                             |
| Dual-polarization (CC, spectrum width) | March 2013                                               |
| Upper air soundings                    | 1989+ (Wyoming archive), coverage varies by station/date |
| NWS DAT tornado surveys                | ~2011+, consistent coverage from ~2015                   |
| SPC outlook shapefiles                 | January 2003                                             |
| SPC outlook GeoJSON                    | ~2020                                                    |

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

Reports take about 2 minutes to generate for most events. Progress is shown live on the status page with a time estimate.

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
│   │   ├── billion_dollar.py       # NOAA Billion-Dollar Disasters lookup
│   │   ├── dat.py                  # NWS Damage Assessment Toolkit ArcGIS client
│   │   ├── discovery.py            # auto-discover warnings at a point
│   │   ├── geocoding.py            # Nominatim + timezone + state/county parsing
│   │   ├── iem.py                  # IEM warnings + LSRs
│   │   ├── ncei.py                 # NCEI Storm Events CSV client
│   │   ├── nexrad_stations.json    # NEXRAD station metadata
│   │   ├── nexrad.py               # NEXRAD Level II from S3
│   │   ├── radar_locator.py        # nearest NEXRAD lookup
│   │   ├── sounding.py             # IEM RAOB + Wyoming fallback, MetPy Skew-T, VAD hodograph
│   │   └── spc_outlook.py          # SPC Day 1 outlook GeoJSON + shapefile fallback
│   │
│   ├── llm/
│   │   ├── base.py                 # Base runner/loader
│   │   ├── anthropic_client.py     # Anthropic setup
│   │   ├── gemini_client.py        # Gemini setup
│   │   └── openai_client.py        # OpenAI setup
│   │
│   ├── radar/
│   │   ├── processor.py            # Py-ART feature extraction
│   │   └── renderer.py             # Cartopy quad-panel rendering
│   │
│   ├── report/
│   │   ├── builder.py              # EventReport dataclass + summary + lead time + LLM prompt
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
│   └── download_ncei.py            # bulk NCEI CSV downloader
│
├── events/                         # JSON event configs (for CLI use)
├── .cache/                         # downloaded radar + NCEI CSVs + soundings (gitignored)
└── output/                         # generated reports (gitignored)
```

## Tested Events

| Event                       | Date              | Notes                                    |
| --------------------------- | ----------------- | ---------------------------------------- |
| Joplin, MO tornado          | May 22, 2011      | EF5, 158 direct fatalities               |
| Moore, OK tornado           | May 20, 2013      | EF5, first dual-pol test                 |
| Hackleburg, AL tornado      | April 27, 2011    | Part of 2011 Super Outbreak              |
| Greenfield, IA tornado      | May 21, 2024      | EF4, DOW-measured 300+ mph winds         |
| Mayfield, KY tornado        | December 10, 2021 | EF4, night event, long track             |
| Rolling Fork, MS tornado    | March 24, 2023    | EF4                                      |
| Greentown, IN tornado       | June 11, 1998     | F3, pre-VTEC-polygon era test            |
| Enderlin, ND tornado        | June 20, 2025     | EF5, train derailment                    |
| Somerset-London, KY tornado | May 16, 2025      | EF4, 19 fatalities, cross-midnight event |

## Roadmap

**Done:**
- End-to-end pipeline (warnings -> LSRs -> NCEI -> DAT -> SPC -> sounding -> radar -> LLM -> report)
- Auto-discovery of warnings via 9-point grid search
- S3 radar fallback loop (tries nearest radar, falls back if no archive data)
- Feature gating with graceful degradation for historical events
- Dual-polarization radar panels (CC + spectrum width) for 2013+ events
- Cartopy map overlay with dark background, county/state lines, city labels
- Parallel radar rendering via ProcessPoolExecutor
- Background jobs with real-time progress updates and time estimates
- Report caching + gallery with EF rating, deaths, and max dBZ on cards
- NCEI Storm Events integration (multi-county, episode IDs, polygon filtering)
- Warning lead time calculation with episode deduplication and data-quality flagging
- Billion-Dollar Disasters outbreak context lookup
- Pre-event sounding: IEM RAOB with Wyoming fallback, MetPy indices, Skew-T with parcel path and CAPE/CIN shading
- VAD hodograph from event-time radar velocity field + SRH computation
- Impact summary card with DAT path length priority
- NWS DAT tornado track integration (curved multi-point geometry, EF override from survey comments, distance filter)
- SPC Day 1 convective outlook overlay (GeoJSON + shapefile fallback, legacy DN mapping, modern/legacy legend)
- NCEI polygon filter with EF3+ rescue clause
- Concurrent pipeline prevention
- Raw radar scan cleanup after rendering

**Planned:**
- Multi-radar support for long-track events
- Storm-relative velocity (SRV) product
- Automated tests
- About page
- Google OAuth + tiered accounts (free/monthly/lifetime)
- Stripe integration
- Cache eviction policy (tiered by EF rating)

## Notes

- Reports are generated on-demand and cached. Re-requesting the same event name redirects to the existing report instantly.
- Only one pipeline runs at a time. Concurrent submissions return a 429 with a retry message.
- NEXRAD archive starts June 1991. Events before that date are not supported.
- NCEI Storm Events data lags 4-8 weeks for recent events. The pipeline falls back to LSR data when NCEI has no record yet.
- DAT tornado track geometry is available for most significant events from ~2011 onward. Pre-DAT events fall back to NCEI straight-line tracks.
- NCEI tornado records are split by county. The impact summary uses DAT path length when available, otherwise the longest single county segment.
- NCEI property damage estimates are unreliable for complex outbreaks and are suppressed when clearly incomplete relative to event severity.
- The event narrative is AI-generated from structured data and may contain errors. The structured data sections (NCEI, warnings, LSRs, radar table) are authoritative.
- LLM API keys are user-supplied (BYOK) when self-hosting. The tool is model-agnostic - Claude, GPT, and Gemini are all supported.

## License

MIT License with Commons Clause. Free for personal use and self-hosting. Commercial use requires a separate license - contact [rwdorrington@gmail.com].

See [LICENSE](LICENSE) for full terms.

## Resources

**Primary data sources:**
- NEXRAD Level II radar: [noaa-nexrad-level2](https://noaa-nexrad-level2.s3.amazonaws.com)
- IEM Mesonet (warnings + LSRs + soundings): [mesonet.agron.iastate.edu](https://mesonet.agron.iastate.edu)
- University of Wyoming (soundings): [weather.uwyo.edu](https://weather.uwyo.edu/upperair/sounding.shtml)
- NCEI Storm Events CSV: [ncei.noaa.gov](https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/)
- NWS Damage Assessment Toolkit: [services.dat.noaa.gov](https://services.dat.noaa.gov/arcgis/rest/services/nws_damageassessmenttoolkit/DamageViewer/FeatureServer)
- SPC Outlook Archive: [spc.noaa.gov](https://www.spc.noaa.gov/products/outlook/archive/)
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