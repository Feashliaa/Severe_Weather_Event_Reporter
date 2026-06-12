"""Upper air sounding data from IEM RAOB archive

Fetches pre-event atmospheric profile, and computes severe weather indices.

With that it renders a Skew-T diagram and a report of the indices.

"""

import math, httpx, pyart, matplotlib, json
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import pandas as pd
import numpy as np
import metpy.calc as mpcalc

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from src import config

from metpy.calc import (
    cape_cin,
    parcel_profile,
)
from metpy.units import units as munits
from metpy.plots import SkewT, Hodograph
from siphon.simplewebservice.wyoming import WyomingUpperAir

RAOB_ENDPOINT = "https://mesonet.agron.iastate.edu/json/raob.py"
SOUNDING_CACHE_DIR = config.CACHE_DIR / "soundings"

# CONUS upper air stations - ICAO id, lat, lon
# Source: NWS upper air network, stable since ~1990
_UA_STATIONS = [
    ("BNA", 36.25, -86.57),  # Nashville TN
    ("OAX", 41.32, -96.37),  # Omaha NE
    ("TOP", 39.07, -95.62),  # Topeka KS
    ("SGF", 37.23, -93.40),  # Springfield MO
    ("ILX", 40.15, -89.34),  # Lincoln IL
    ("DVN", 41.61, -90.58),  # Davenport IA
    ("FWD", 32.83, -97.30),  # Fort Worth TX
    ("OUN", 35.18, -97.44),  # Norman OK
    ("DDC", 37.77, -99.97),  # Dodge City KS
    ("LBF", 41.13, -100.68),  # North Platte NE
    ("ABR", 45.45, -98.41),  # Aberdeen SD
    ("MPX", 44.85, -93.57),  # Minneapolis MN
    ("GRB", 44.50, -88.13),  # Green Bay WI
    ("DTX", 42.70, -83.47),  # Detroit MI
    ("BUF", 42.93, -78.73),  # Buffalo NY
    ("ALB", 42.75, -73.80),  # Albany NY
    ("CHH", 41.67, -69.97),  # Chatham MA
    ("OKX", 40.87, -72.87),  # Upton NY
    ("IAD", 38.98, -77.45),  # Sterling VA
    ("RNK", 37.20, -80.42),  # Blacksburg VA
    ("GSO", 36.10, -79.95),  # Greensboro NC
    ("CHS", 32.90, -80.03),  # Charleston SC
    ("JAX", 30.49, -81.70),  # Jacksonville FL
    ("TBW", 27.71, -82.40),  # Tampa FL
    ("XMR", 28.47, -80.57),  # Cape Canaveral FL
    ("TLH", 30.38, -84.37),  # Tallahassee FL
    ("BMX", 33.17, -86.77),  # Birmingham AL
    ("JAN", 32.32, -90.08),  # Jackson MS
    ("LIX", 30.33, -89.83),  # New Orleans LA
    ("SHV", 32.45, -93.82),  # Shreveport LA
    ("AMA", 35.23, -101.70),  # Amarillo TX
    ("MAF", 31.94, -102.19),  # Midland TX
    ("DRT", 29.37, -100.92),  # Del Rio TX
    ("BRO", 25.92, -97.42),  # Brownsville TX
    ("CRP", 27.77, -97.51),  # Corpus Christi TX
    ("FFC", 33.36, -84.57),  # Peachtree City GA
    ("ILN", 39.42, -83.82),  # Wilmington OH
    ("PIT", 40.53, -80.23),  # Pittsburgh PA
    ("WAL", 37.93, -75.48),  # Wallops Island VA
    ("CAR", 46.87, -68.02),  # Caribou ME
    ("GYX", 43.89, -70.26),  # Gray ME
    ("ALY", 42.75, -73.80),  # Albany NY
    ("MPV", 44.20, -72.56),  # Montpelier VT
    ("GJT", 39.12, -108.53),  # Grand Junction CO
    ("DNR", 39.75, -104.87),  # Denver CO
    ("UNR", 44.07, -103.22),  # Rapid City SD
    ("BIS", 46.77, -100.75),  # Bismarck ND
    ("GGW", 48.21, -106.62),  # Glasgow MT
    ("TFX", 47.46, -111.38),  # Great Falls MT
    ("SLC", 40.77, -111.97),  # Salt Lake City UT
    ("BOI", 43.57, -116.22),  # Boise ID
    ("GEG", 47.63, -117.53),  # Spokane WA
    ("UIL", 47.95, -124.55),  # Quillayute WA
    ("REV", 39.57, -119.80),  # Reno NV
    ("VBG", 34.73, -120.57),  # Vandenberg CA
    ("NKX", 32.87, -117.13),  # San Diego CA
    ("OAK", 37.73, -122.22),  # Oakland CA
    ("MFR", 42.37, -122.87),  # Medford OR
    ("OTX", 47.68, -117.63),  # Spokane WA
    ("FGZ", 35.23, -111.82),  # Flagstaff AZ
    ("TUS", 32.12, -110.92),  # Tucson AZ
    ("EPZ", 31.87, -106.70),  # El Paso TX
    ("ABQ", 35.05, -106.53),  # Albuquerque NM
    ("AHN", 33.95, -83.32),  # Athens GA
    ("LZK", 34.84, -92.26),  # Little Rock AR
    ("SGF", 37.23, -93.40),  # Springfield MO
    ("LSX", 38.69, -90.68),  # St Louis MO
    ("JEF", 38.82, -92.56),  # Jefferson City MO
    ("IRX", 44.68, -84.47),  # Gaylord MI
    ("APX", 44.91, -84.72),  # Gaylord MI
    ("MHX", 34.78, -76.88),  # Newport NC
    ("RAX", 35.66, -78.49),  # Raleigh NC
]


def compute_kinematics_from_sounding(sounding_data: dict) -> dict:
    """Compute true ambient SRH parameters using modern MetPy core signatures."""
    try:
        profile = sounding_data["profile"]

        # Filter layers missing critical data
        valid_levels = [
            l
            for l in profile
            if l["hght"] is not None and l["drct"] is not None and l["sknt"] is not None
        ]
        if not valid_levels:
            print("    [ERROR] Sounding kinematic engine found 0 valid height records.")
            return {}

        # 1. Parse arrays and append strict units
        heights = np.array([l["hght"] for l in valid_levels]) * munits.m
        directions = np.array([l["drct"] for l in valid_levels]) * munits.deg
        speeds = np.array([l["sknt"] for l in valid_levels]) * munits.knots

        # 2. Deconstruct speeds/directions into U/V components
        u, v = mpcalc.wind_components(speeds, directions)

        # 3. Create dummy pressure array needed for the modern Bunkers signature
        # (bunkers_storm_motion requires: pressure, u, v, height)
        # We can extract actual pressures if available, or fake it safely for standard heights
        pressures = (
            np.array(
                [
                    l["pres"] if l["pres"] is not None else 1000.0 - (idx * 5)
                    for idx, l in enumerate(valid_levels)
                ]
            )
            * munits.hPa
        )

        # 4. Extract Bunkers Storm Motion Vectors
        # Returns: (right_mover, left_mover, wind_mean)
        bunkers_output = mpcalc.bunkers_storm_motion(pressures, u, v, heights)
        bunkers_right = bunkers_output[0]  # Index 0 is Right Mover [u_comp, v_comp]

        # Extract strict dimensional U and V motion velocities
        bunkers_u = bunkers_right[0]
        bunkers_v = bunkers_right[1]

        # 5. Compute SRH using modern keyword-only storm_u and storm_v definitions
        # Returns tuple: (positive_srh, negative_srh, total_srh)
        srh_01_tuple = mpcalc.storm_relative_helicity(
            heights, u, v, depth=1000 * munits.m, storm_u=bunkers_u, storm_v=bunkers_v
        )

        srh_03_tuple = mpcalc.storm_relative_helicity(
            heights, u, v, depth=3000 * munits.m, storm_u=bunkers_u, storm_v=bunkers_v
        )

        # Index [2] returns Total SRH magnitude
        return {
            "srh_01km": round(float(srh_01_tuple[2].magnitude)),
            "srh_03km": round(float(srh_03_tuple[2].magnitude)),
        }

    except Exception as e:
        print(f"    Sounding kinematics calculation failed: {e}")
        return {}


def render_hodograph_from_sounding(
    sounding_data: dict, output_path: Path
) -> Path | None:
    """Render a clean environmental Hodograph using the RAOB sounding data."""
    try:
        profile = sounding_data["profile"]
        valid_levels = [
            l
            for l in profile
            if l["hght"] is not None and l["drct"] is not None and l["sknt"] is not None
        ]
        if not valid_levels:
            return None

        heights = np.array([l["hght"] for l in valid_levels]) * munits.m
        directions = np.array([l["drct"] for l in valid_levels]) * munits.deg
        speeds = np.array([l["sknt"] for l in valid_levels]) * munits.knots

        u, v = mpcalc.wind_components(speeds, directions)

        fig, ax = plt.subplots(figsize=(5, 5), dpi=90)
        fig.patch.set_facecolor("#1c2b3a")
        ax.set_facecolor("#1c2b3a")

        h_obj = Hodograph(ax, component_range=60)
        h_obj.add_grid(increment=10, color="white", alpha=0.2)
        h_obj.plot_colormapped(u, v, heights, cmap="jet")

        ax.tick_params(colors="white")
        for spine in ax.spines.values():
            spine.set_edgecolor("#3a4a5c")

        plt.title(f"{sounding_data['station']} Hodograph", color="white", fontsize=10)
        plt.tight_layout()

        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=90, facecolor="#1c2b3a", bbox_inches="tight")
        plt.close(fig)
        return output_path
    except Exception as e:
        print(f"    Sounding Hodograph render failed: {e}")
        return None


def extract_vad(radar_path: Path) -> dict | None:
    """Extract VAD wind profile from a NEXRAD Level II File."""
    try:
        radar = pyart.io.read_nexrad_archive(str(radar_path))
        if "velocity" not in radar.fields:
            return None

        vad = pyart.retrieve.vad_browning(
            radar, "velocity", z_want=np.arange(500, 10000, 500)
        )

        mask = ~np.ma.getmaskarray(np.ma.array(vad.u_wind))
        if not mask.any():
            return None

        return {
            "u_wind": [float(x) for x in np.array(vad.u_wind)[mask]],
            "v_wind": [float(x) for x in np.array(vad.v_wind)[mask]],
            "height_m": [float(x) for x in np.array(vad.height)[mask]],
        }
    except Exception as e:
        print(f"    Vad extraction failed: {e}")
        return None


def compute_srh(vad_data: dict) -> dict:
    """Compute SRH from VAD wind profile"""
    try:
        u = np.array(vad_data["u_wind"]) * munits("m/s")
        v = np.array(vad_data["v_wind"]) * munits("m/s")
        h = np.array(vad_data["height_m"]) * munits("m")

        srh_01 = mpcalc.storm_relative_helicity(h, u, v, depth=1000 * munits.m)
        srh_03 = mpcalc.storm_relative_helicity(h, u, v, depth=3000 * munits.m)

        return {
            "srh_01km": round(float(srh_01[2].magnitude)),
            "srh_03km": round(float(srh_03[2].magnitude)),
        }
    except Exception as e:
        print(f"    SRH computation failed: {e}")
        return {}


def render_hodograph(vad_data: dict, output_path: Path) -> Path | None:
    """Render Hodograph from VAD wind profile."""
    try:
        u = np.array(vad_data["u_wind"]) * munits("m/s")
        v = np.array(vad_data["v_wind"]) * munits("m/s")
        h = np.array(vad_data["height_m"]) * munits("m")

        fig, ax = plt.subplots(figsize=(5, 5), dpi=90)
        fig.patch.set_facecolor("#1c2b3a")
        ax.set_facecolor("#1c2b3a")

        h_obj = Hodograph(ax, component_range=50)
        h_obj.add_grid(increment=10, color="white", alpha=0.2)
        h_obj.plot_colormapped(u, v, h, cmap="jet")

        ax.tick_params(colors="white")
        for spine in ax.spines.values():
            spine.set_edgecolor("#3a4a5c")

        plt.title("VAD Hodograph", color="white", fontsize=10)
        plt.tight_layout()

        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=90, facecolor="#1c2b3a", bbox_inches="tight")
        plt.close(fig)
        return output_path
    except Exception as e:
        print(f"    Hodograph render failed: {e}")
        return None


def _nearest_station(lat: float, lon: float) -> str | None:
    best, best_dist = None, float("inf")
    coslat = math.cos(math.radians(lat))
    for station_id, slat, slon in _UA_STATIONS:
        dist = math.hypot(lat - slat, (lon - slon) * coslat)
        if dist < best_dist:
            best_dist, best = dist, station_id
    return best


def _pick_sounding_time(event_start: datetime) -> list[str]:
    """Return candidate sounding times to try, in preference order.

    Generates an aggressive cascade of 4 standard RAOB cycles (00Z, 12Z, 18Z)
    to handle convective contamination gracefully.
    """
    hour = event_start.hour
    base_date = event_start.date()

    dt_00z = datetime.combine(base_date, datetime.min.time())
    dt_12z = datetime.combine(base_date, time(12, 0))

    if hour < 12:
        # Event is overnight UTC (e.g., 00:45Z on the 25th)
        prev_day = base_date - timedelta(days=1)
        dt_prev_12z = datetime.combine(prev_day, time(hour=12))
        dt_prev_18z = datetime.combine(prev_day, time(hour=18))

        # 00Z is the absolute target window here (matches the write-up's chart)
        return [
            dt_00z.strftime(
                "%Y%m%d%H"
            ),  # 1. Day-of 00Z (Captured just before/at event window)
            dt_prev_18z.strftime("%Y%m%d%H"),  # 2. Late afternoon ambient air
            dt_prev_12z.strftime("%Y%m%d%H"),  # 3. Morning baseline
            dt_12z.strftime("%Y%m%d%H"),  # 4. Last resort fallback
        ]
    else:
        # Event is afternoon/evening UTC (e.g., 18:00Z on the 24th)
        dt_18z = datetime.combine(base_date, time(18, 0))
        next_00z = dt_00z + timedelta(days=1)

        # Preference:
        # 1. Day-of 12Z
        # 2. Day-of 18Z
        # 3. Day-of 00Z
        # 4. Next-day 00Z
        return [
            dt_12z.strftime("%Y%m%d%H"),
            dt_18z.strftime("%Y%m%d%H"),
            dt_00z.strftime("%Y%m%d%H"),
            next_00z.strftime("%Y%m%d%H"),
        ]


def fetch_sounding(lat: float, lon: float, event_start: datetime) -> dict | None:
    """Fetch nearest pre-event sounding with extensive diagnostic logging."""
    print(f"\n[SOUNDING DIAGNOSTIC] Starting lookup for Coords: ({lat}, {lon})")
    print(
        f"[SOUNDING DIAGNOSTIC] Event start timestamp parsed: {event_start.strftime('%Y-%m-%d %H:%M:%SZ')}"
    )

    station = _nearest_station(lat, lon)
    if station is None:
        print("  [ERROR] Sounding: no station found near coordinates.")
        return None
    print(f"[SOUNDING DIAGNOSTIC] Nearest upper-air station determined: {station}")

    SOUNDING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    candidates = _pick_sounding_time(event_start)
    print(f"[SOUNDING DIAGNOSTIC] Generated candidate cascade order: {candidates}")

    for idx, ts in enumerate(candidates, start=1):
        dt = datetime.strptime(ts, "%Y%m%d%H").replace(tzinfo=timezone.utc)
        print(f"\n  --- Trying Candidate #{idx}: {ts}Z ---")

        # ==========================================
        # 1. TRY IEM
        # ==========================================
        iem_cache = SOUNDING_CACHE_DIR / f"{station}_{ts}.json"
        if iem_cache.exists():
            print(f"    [CACHE HIT] Found IEM file locally: {iem_cache.name}")
            try:
                data = json.loads(iem_cache.read_text())
            except Exception as e:
                print(f"    [CACHE ERROR] Failed reading IEM cache json: {e}")
                data = {"profiles": []}
        else:
            print(f"    [CACHE MISS] Fetching from IEM API: {RAOB_ENDPOINT}")
            print(f"    [API URL] {RAOB_ENDPOINT}?ts={ts}&station={station}&fmt=json")
            try:
                r = httpx.get(
                    RAOB_ENDPOINT,
                    params={"ts": ts, "station": station, "fmt": "json"},
                    timeout=30.0,
                )
                print(f"    [API RESPONSE] Status Code: {r.status_code}")
                r.raise_for_status()
                data = r.json()
                iem_cache.write_text(r.text)
                print(f"    [CACHE WRITE] Saved IEM payload to disk.")
            except Exception as e:
                print(f"    [API ERROR] IEM HTTP execution failed: {e}")
                data = {"profiles": []}

        profiles = data.get("profiles", [])
        if profiles:
            levels_count = len(profiles[0].get("profile", []))
            print(
                f"    [SUCCESS] IEM returned active profile for {ts}Z with {levels_count} discrete levels."
            )
            return {
                "station": station,
                "valid": profiles[0].get("valid"),
                "profile": profiles[0]["profile"],
            }
        else:
            print(f"    [NOTICE] IEM payload contained 0 profiles for timestamp {ts}Z.")

        # ==========================================
        # 2. TRY WYOMING FALLBACK
        # ==========================================
        print(f"    [FALLBACK] Transitioning to Wyoming request protocol for {ts}Z...")
        wyo_cache = SOUNDING_CACHE_DIR / f"{station}_{ts}_wyoming.json"
        if wyo_cache.exists():
            print(f"    [CACHE HIT] Found Wyoming file locally: {wyo_cache.name}")
            try:
                result = json.loads(wyo_cache.read_text())
                print(
                    f"    [SUCCESS] Loaded cached Wyoming profile containing {len(result.get('profile', []))} levels."
                )
                return result
            except Exception as e:
                print(f"    [CACHE ERROR] Failed reading Wyoming cache json: {e}")

        try:
            print(f"    [NETWORK] Querying Siphon WyomingUpperAir engine for {dt}...")
            df = WyomingUpperAir.request_data(dt, station)

            if df is not None and not df.empty:
                print(
                    f"    [NETWORK SUCCESS] Wyoming returned DataFrame. Row count: {len(df)}"
                )
                profile = []
                for _, row in df.iterrows():
                    profile.append(
                        {
                            "pres": (
                                float(row["pressure"])
                                if not pd.isna(row["pressure"])
                                else None
                            ),
                            "hght": (
                                float(row["height"])
                                if not pd.isna(row["height"])
                                else None
                            ),
                            "tmpc": (
                                float(row["temperature"])
                                if not pd.isna(row["temperature"])
                                else None
                            ),
                            "dwpc": (
                                float(row["dewpoint"])
                                if not pd.isna(row["dewpoint"])
                                else None
                            ),
                            "drct": (
                                float(row["direction"])
                                if not pd.isna(row["direction"])
                                else None
                            ),
                            "sknt": (
                                float(row["speed"])
                                if not pd.isna(row["speed"])
                                else None
                            ),
                        }
                    )

                result = {
                    "station": station,
                    "valid": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "profile": profile,
                    "source": "wyoming",
                }
                wyo_cache.write_text(json.dumps(result))
                print(
                    f"    [SUCCESS] Parsed and saved Wyoming profile with {len(profile)} levels."
                )
                return result
            else:
                print(
                    f"    [NOTICE] Wyoming engine returned an empty/None dataset for {station} at {ts}Z."
                )
        except Exception as e:
            print(
                f"    [API ERROR] Siphon Wyoming engine execution failed for {station} {ts}Z: {e}"
            )

        print(
            f"  [CYCLE WRAP] Candidate {ts}Z exhausted. Moving to next sequence entry..."
        )

    print(
        f"\n[FATAL] Sounding loop complete. Exhausted all candidates without acquiring data."
    )
    return None


def compute_indices(sounding: dict) -> dict:
    """Compute severe weather indices from sounding profile."""
    try:
        profile = sounding["profile"]

        # Filter to levels with complete thermodynamic data
        thermo = [
            (p["pres"], p["hght"], p["tmpc"], p["dwpc"])
            for p in profile
            if all(p.get(k) is not None for k in ["pres", "hght", "tmpc", "dwpc"])
        ]

        # Filter to levels with wind data
        wind_levels = [
            (p["pres"], p["drct"], p["sknt"])
            for p in profile
            if all(p.get(k) is not None for k in ["pres", "drct", "sknt"])
        ]

        if len(thermo) < 5:
            return {}

        pres = np.array([t[0] for t in thermo]) * munits.hPa
        hght = np.array([t[1] for t in thermo]) * munits.meters
        tmpc = np.array([t[2] for t in thermo]) * munits.degC
        dwpc = np.array([t[3] for t in thermo]) * munits.degC

        # CAPE and CIN
        parcel = parcel_profile(pres, tmpc[0], dwpc[0])
        cape, cin = cape_cin(pres, tmpc, dwpc, parcel)

        indices = {
            "station": sounding["station"],
            "valid": sounding["valid"],
            "cape_jkg": round(float(cape.magnitude)),
            "cin_jkg": round(float(cin.magnitude)),
        }

        # 0-6km bulk shear
        if len(wind_levels) >= 3:
            wpres = np.array([w[0] for w in wind_levels]) * munits.hPa
            wdrct = np.array([w[1] for w in wind_levels]) * munits.degrees
            wsknt = np.array([w[2] for w in wind_levels]) * munits.knots

            # Convert to u/v components
            u = -wsknt * np.sin(np.radians(wdrct.magnitude))
            v = -wsknt * np.cos(np.radians(wdrct.magnitude))

            # Simple 0-6km bulk shear estimate
            # Find surface and ~6km level
            sfc_u, sfc_v = float(u[0].magnitude), float(v[0].magnitude)

            # Find wind at ~6km - use pressure ~500 hPa as proxy
            idx_500 = np.argmin(np.abs(wpres.magnitude - 500))
            top_u = float(u[idx_500].magnitude)
            top_v = float(v[idx_500].magnitude)

            shear_mag = math.sqrt((top_u - sfc_u) ** 2 + (top_v - sfc_v) ** 2)
            indices["bulk_shear_06km_kt"] = round(shear_mag)

        # Surface conditions
        indices["sfc_temp_c"] = round(thermo[0][2], 1)
        indices["sfc_dewpoint_c"] = round(thermo[0][3], 1)
        indices["sfc_pressure_hpa"] = thermo[0][0]

        return indices

    except Exception as e:
        print(f"  Sounding indices failed: {e}")
        return {}


def render_skewt(sounding: dict, output_path: Path) -> Path | None:
    """Render a Skew-T log-P diagram and save as PNG."""
    try:
        profile = sounding["profile"]
        thermo = [
            (p["pres"], p["tmpc"], p["dwpc"])
            for p in profile
            if all(p.get(k) is not None for k in ["pres", "tmpc", "dwpc"])
        ]
        wind_levels = [
            (p["pres"], p["drct"], p["sknt"])
            for p in profile
            if all(p.get(k) is not None for k in ["pres", "drct", "sknt"])
        ]
        if len(thermo) < 5:
            return None

        pres = np.array([t[0] for t in thermo]) * munits.hPa
        tmpc = np.array([t[1] for t in thermo]) * munits.degC
        dwpc = np.array([t[2] for t in thermo]) * munits.degC

        fig = plt.figure(figsize=(10, 7), dpi=90)
        fig.patch.set_facecolor("#1c2b3a")

        gs = fig.add_gridspec(
            1,
            2,
            width_ratios=[5, 1],
            wspace=0.02,
            left=0.05,
            right=0.95,
            top=0.93,
            bottom=0.07,
        )

        skew = SkewT(fig, rotation=45, subplot=gs[0])
        skew.ax.set_facecolor("#1c2b3a")

        skew.plot(pres, tmpc, "r", linewidth=2, label="Temperature")
        skew.plot(pres, dwpc, "g", linewidth=2, label="Dewpoint")

        parcel = parcel_profile(pres, tmpc[0], dwpc[0])
        skew.plot(pres, parcel.to("degC"), "k--", linewidth=1.5, label="Parcel Path")
        skew.shade_cape(pres, tmpc, parcel)
        skew.shade_cin(pres, tmpc, parcel, dwpc)

        skew.plot_dry_adiabats(alpha=0.3, colors="#ff6b35")
        skew.plot_moist_adiabats(alpha=0.3, colors="#4ecdc4")
        skew.plot_mixing_lines(alpha=0.3, colors="#95e1d3")

        skew.ax.set_ylim(1000, 100)
        skew.ax.set_xlim(-40, 50)
        skew.ax.tick_params(colors="white")
        skew.ax.xaxis.label.set_color("white")
        skew.ax.xaxis.label.set_text("Temperature (°C)")
        skew.ax.yaxis.label.set_text("Pressure (hPa)")
        skew.ax.yaxis.label.set_color("white")
        for spine in skew.ax.spines.values():
            spine.set_edgecolor("#3a4a5c")

        # Wind barb panel
        ax_barbs = fig.add_subplot(gs[1])
        ax_barbs.set_facecolor("#1c2b3a")
        ax_barbs.set_ylim(1050, 100)
        ax_barbs.set_xlim(-1, 1)
        ax_barbs.set_xticks([])
        ax_barbs.set_yticks([])
        ax_barbs.tick_params(axis="y", left=False, right=False)
        ax_barbs.set_title("Wind\n(kt)", color="white", fontsize=7, pad=4)
        ax_barbs.set_clip_on(True)
        for spine in ax_barbs.spines.values():
            spine.set_edgecolor("#3a4a5c")

        if wind_levels:
            wpres = np.array([w[0] for w in wind_levels]) * munits.hPa
            wdrct = np.array([w[1] for w in wind_levels]) * munits.degrees
            wsknt = np.array([w[2] for w in wind_levels]) * munits.knots
            u = -wsknt * np.sin(np.radians(wdrct.magnitude))
            v = -wsknt * np.cos(np.radians(wdrct.magnitude))
            mask = (wpres.magnitude >= 100) & (wpres.magnitude <= 1000)
            ax_barbs.barbs(
                np.zeros(len(wpres[mask][::3])),
                wpres[mask][::3].magnitude,
                u[mask][::3].magnitude,
                v[mask][::3].magnitude,
                color="white",
                length=6,
                linewidth=0.8,
            )

        title = f"Sounding | {sounding['station']} {sounding.get('valid', '')[:13].replace('T', ' ')}Z"
        fig.suptitle(title, color="white", fontsize=11, y=0.98)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=90, facecolor="#1c2b3a")
        plt.close(fig)
        return output_path

    except Exception as e:
        print(f"  Skew-T render failed: {e}")
        return None
