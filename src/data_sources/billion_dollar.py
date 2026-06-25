# src/data_sources/billion_dollar.py
import csv
from datetime import date
from pathlib import Path

CACHE_PATH = Path(".cache/billion_dollar_disasters.csv")

_DISASTERS: list[dict] | None = None

RELEVANT_TYPES = {"Severe Storm", "Tornado", "Flooding"}

def _load() -> list[dict]:
    global _DISASTERS
    if _DISASTERS is not None:
        return _DISASTERS
    if not CACHE_PATH.exists():
        return []
    disasters = []
    with open(CACHE_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("Disaster") not in RELEVANT_TYPES:
                continue
            try:
                disasters.append({
                    "name": row["Name"],
                    "disaster": row["Disaster"],
                    "begin": date(int(row["Begin Date"][:4]), int(row["Begin Date"][4:6]), int(row["Begin Date"][6:8])),
                    "end": date(int(row["End Date"][:4]), int(row["End Date"][4:6]), int(row["End Date"][6:8])),
                    "cost_adjusted": float(row["CPI-Adjusted Cost"]),
                    "cost_unadjusted": float(row["Unadjusted Cost"]),
                    "deaths": int(row["Deaths"]),
                })
            except (ValueError, KeyError):
                continue
    _DISASTERS = disasters
    return disasters

def lookup(event_date: date) -> dict | None:
    matches = [d for d in _load() if d["begin"] <= event_date <= d["end"]]
    if not matches:
        return None
    return min(matches, key=lambda d: (d["end"] - d["begin"]).days)