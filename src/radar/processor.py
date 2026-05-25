"""Radar feature extraction using Py-ART.

The LLM should never look at radar images and try to interpret them — that's
where hallucinations live. Instead, this module pre-computes the meteorologically
important features and hands the LLM structured numbers.
"""
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
import sys, json, pyart, numpy as np

MAX_PLAUSIBLE_KFT = 70.0
MAX_PLAUSIBLE_DBZ = 75.0
MIN_PLAUSIBLE_CORE_HEIGHT_KFT = 3.0

@dataclass
class RadarFeatures:
    """Numeric features extracted from a single Level II volume scan."""
    timestamp: str
    site: str
    filename: str
    # Reflectivity (Z) features
    max_reflectivity_dbz: float | None
    max_reflectivity_height_kft: float | None
    echo_top_18dbz_kft: float | None  # height of 18 dBZ echo
    echo_top_50dbz_kft: float | None  # height of 50 dBZ echo (strong core)
    # Velocity features (if available)
    max_inbound_velocity_kt: float | None
    max_outbound_velocity_kt: float | None
    # Diagnostic
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _meters_to_kft(m: float) -> float:
    return round(m * 3.28084 / 1000.0, 1)


def _ms_to_kt(ms: float) -> float:
    return round(ms * 1.94384, 1)


def extract_features(level2_path: Path) -> RadarFeatures:
    """Process a Level II volume scan and extract key features.

    Py-ART reads the volume into a Radar object with `fields` dict containing
    reflectivity, velocity, etc. We aggregate across all sweeps to get
    volume-level extremes.
    """
    radar = pyart.io.read_nexrad_archive(str(level2_path))

    timestamp = pyart.util.datetime_from_radar(radar).isoformat() # type: ignore
    site = radar.metadata.get("instrument_name", "unknown")
    if isinstance(site, bytes):
        site = site.decode()

    notes_parts = []

    # --- Reflectivity ---
    max_z = max_z_height = echo_top_18 = echo_top_50 = None
    if "reflectivity" in radar.fields:
        z = radar.fields["reflectivity"]["data"]
        gate_alt = radar.gate_altitude["data"]

        valid = ~np.ma.getmaskarray(z)
        if valid.any():
            max_z_idx = np.unravel_index(np.ma.argmax(z), z.shape)
            max_z = float(z[max_z_idx])
            max_z_height = _meters_to_kft(float(gate_alt[max_z_idx]))

            for threshold, label in [(18.0, "18"), (50.0, "50")]:
                mask = z >= threshold
                if mask.any():
                    heights_above_threshold = gate_alt[mask]
                    top_m = float(np.max(heights_above_threshold))
                    if label == "18":
                        echo_top_18 = _meters_to_kft(top_m)
                    else:
                        echo_top_50 = _meters_to_kft(top_m)
    else:
        notes_parts.append("no reflectivity field")

    # Sanity-cap implausible values (separate from field-presence check)
    if echo_top_18 is not None and echo_top_18 > MAX_PLAUSIBLE_KFT:
        notes_parts.append(f"18dBZ top capped (raw {echo_top_18} kft suggests artifact)")
        echo_top_18 = None
    if echo_top_50 is not None and echo_top_50 > MAX_PLAUSIBLE_KFT:
        notes_parts.append(f"50dBZ top capped (raw {echo_top_50} kft suggests artifact)")
        echo_top_50 = None
    if max_z is not None and max_z > MAX_PLAUSIBLE_DBZ:
        notes_parts.append(f"max_z capped (raw {max_z} dBZ suggests artifact)")
        max_z = MAX_PLAUSIBLE_DBZ  # or set to None
    if max_z_height is not None and max_z_height < MIN_PLAUSIBLE_CORE_HEIGHT_KFT:
        notes_parts.append(f"core height suspicious ({max_z_height} kft)")
        max_z_height = None

    # --- Velocity ---
    max_in = max_out = None
    if "velocity" in radar.fields:
        v = radar.fields["velocity"]["data"]
        if not np.ma.getmaskarray(v).all():
            max_in = _ms_to_kt(float(np.ma.min(v)))   # most negative = inbound
            max_out = _ms_to_kt(float(np.ma.max(v)))  # most positive = outbound
    else:
        notes_parts.append("no velocity field")

    return RadarFeatures(
        timestamp=timestamp,
        site=site,
        filename=level2_path.name,
        max_reflectivity_dbz=round(max_z, 1) if max_z is not None else None,
        max_reflectivity_height_kft=max_z_height,
        echo_top_18dbz_kft=echo_top_18,
        echo_top_50dbz_kft=echo_top_50,
        max_inbound_velocity_kt=max_in,
        max_outbound_velocity_kt=max_out,
        notes="; ".join(notes_parts),
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m src.radar.processor <path_to_level2_file>")
        sys.exit(1)
    features = extract_features(Path(sys.argv[1]))
    print(json.dumps(features.to_dict(), indent=2))