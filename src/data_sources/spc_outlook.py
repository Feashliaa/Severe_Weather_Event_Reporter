"""SPC Day 1 Convective Outlook client.
GeoJSON available ~2020+. Shapefile fallback for older events back to 2003.
"""

import io, json, os, tempfile, zipfile, httpx
from datetime import datetime, timedelta
from pathlib import Path
from src import config
import geopandas as gpd

SPC_ARCHIVE_BASE = "https://www.spc.noaa.gov/products/outlook/archive"
OUTLOOK_CACHE_DIR = config.CACHE_DIR / "SPC_OUTLOOK"

_ISSUANCE_TIMES = [100, 1200, 1300, 1630, 2000]

DN_MAP = {
    # Modern scheme (post Oct 2014)
    2: ("TSTM", "General Thunderstorms", "#55BB55", "#C1E9C1"),
    3: ("MRGL", "Marginal Risk", "#005500", "#66A366"),
    4: ("SLGT", "Slight Risk", "#DDAA00", "#FFE066"),
    5: ("ENH", "Enhanced Risk", "#FF6600", "#FFA366"),
    6: ("MDT", "Moderate Risk", "#CC0000", "#E06666"),
    7: ("HIGH", "High Risk", "#FF00FF", "#FF00FF"),
    # Legacy scheme (pre Oct 2014)
    8: ("HIGH", "High Risk", "#FF00FF", "#FF00FF"),
}


def _pick_outlook_product(event_start: datetime) -> tuple[str, str]:
    """Return (date_str, issuance) for the Day 1 outlook covering event_start.

    SPC archive convention: 1200/1300/1630/2000 files are dated by convective
    day; the 0100 file under date D is issued 0100Z on calendar day D and
    covers 0100-1200Z that morning (the previous convective day's overnight).
    """
    hhmm = event_start.hour * 100 + event_start.minute

    if hhmm >= 1200:
        # Daytime/evening: latest same-date product
        candidates = [t for t in (1200, 1300, 1630, 2000) if t <= hhmm]
        issuance = candidates[-1]
        product_date = event_start
    elif hhmm >= 100:
        # Overnight 0100-1200Z: the 0100 product of THIS calendar date
        issuance = 100
        product_date = event_start
    else:
        # 0000-0100Z: latest product is previous date's 2000Z
        issuance = 2000
        product_date = event_start - timedelta(days=1)

    return product_date.strftime("%Y%m%d"), f"{issuance:04d}"


def _from_shapefile(r_content: bytes) -> dict | None:
    """Parse categorical outlook shapefile zip into GeoJSON-like FeatureCollection."""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            z = zipfile.ZipFile(io.BytesIO(r_content))
            z.extractall(tmp)
            cat_shp = next(
                (f for f in os.listdir(tmp) if f.endswith(".shp") and "cat" in f), None
            )
            if not cat_shp:
                return None
            gdf = gpd.read_file(os.path.join(tmp, cat_shp))
            gdf = gdf.to_crs(epsg=4326)
            gdf = gdf.dissolve(by="DN").reset_index()

            features = []
            for _, row in gdf.iterrows():
                dn = int(row["DN"])
                label, label2, stroke, fill = DN_MAP.get(
                    dn, ("UNK", "Unknown", "#999999", "#cccccc")
                )
                features.append(
                    {
                        "type": "Feature",
                        "properties": {
                            "DN": dn,
                            "LABEL": label,
                            "LABEL2": label2,
                            "stroke": stroke,
                            "fill": fill,
                        },
                        "geometry": row.geometry.__geo_interface__,
                    }
                )

            return {"type": "FeatureCollection", "features": features}
    except Exception as e:
        print(f"  SPC shapefile parse failed: {e}")
        return None


def fetch_outlook(event_start: datetime) -> dict | None:
    """
    Fetch SPC Day 1 categorical outlook for the event date.
    Adjusts UTC dates falling between 00Z and 12Z to the previous calendar day
    to match the SPC Convective Day archive.
    """
    OUTLOOK_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    
    date_str, issuance = _pick_outlook_product(event_start=event_start)
    year = date_str[:4]

    print(f"  SPC outlook: selecting product {date_str} {issuance}Z for event at {event_start.strftime('%Y-%m-%d %H:%MZ')}")

    cache_path = OUTLOOK_CACHE_DIR / f"{date_str}_{issuance}_cat.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text())

    # Try GeoJSON first
    geojson_url = (
        f"{SPC_ARCHIVE_BASE}/{year}/day1otlk_{date_str}_{issuance}_cat.lyr.geojson"
    )

    try:
        r = httpx.get(geojson_url, timeout=15.0)
        if r.status_code == 200:
            data = r.json()
            cache_path.write_text(r.text)
            print(f"    SPC outlook: loaded GeoJSON for {date_str} {issuance}Z")
            return data
    except Exception as e:
        print(f"    SPC GeoJSON fetch failed: {e}")

    # Fallback to shpfile
    shp_url = f"{SPC_ARCHIVE_BASE}/{year}/day1otlk_{date_str}_{issuance}-shp.zip"
    try:
        r = httpx.get(shp_url, timeout=30.0)
        if r.status_code == 200:
            data = _from_shapefile(r.content)
            if data:
                cache_path.write_text(json.dumps(data))
                print(f"    SPC outlook: loaded shapefile for {date_str} {issuance}Z")
                return data
    except Exception as e:
        print(f"    SPC shapefile fetch failed: {e}")

    print(f"    SPC outlook: No data for {date_str} {issuance}Z")
    return None
