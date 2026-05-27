import httpx
import re
from pathlib import Path

NCEI_BASE_URL = "https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/"
CACHE_DIR = Path(".cache/ncei")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

print("Fetching directory listing...")
r = httpx.get(NCEI_BASE_URL, timeout=30.0)
pattern = r"StormEvents_details-ftp_v1\.0_d(\d{4})_c\d+\.csv\.gz"
files = re.findall(pattern, r.text)

for year_str in sorted(files):
    year = int(year_str)
    if year < 1991:
        continue
    
    match = re.search(rf"StormEvents_details-ftp_v1\.0_d{year}_c\d+\.csv\.gz", r.text)
    if not match:
        continue
    
    filename = match.group(0)
    dest = CACHE_DIR / filename
    
    if dest.exists():
        print(f"  {filename} already cached, skipping")
        continue
    
    print(f"  Downloading {filename}...")
    try:
        dl = httpx.get(NCEI_BASE_URL + filename, timeout=120.0, follow_redirects=True)
        dl.raise_for_status()
        dest.write_bytes(dl.content)
        print(f"    Done ({len(dl.content) / 1024 / 1024:.1f} MB)")
    except Exception as e:
        print(f"    Failed: {e}")

print("All done.")