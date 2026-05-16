"""Render Level II radar data to PNG images for the report."""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pyart


def _lowest_sweep_with_field(radar, field: str) -> int:
    """Find the lowest-elevation sweep that contains the given field with data."""
    elevations = radar.fixed_angle["data"]
    # Sort sweep indices by elevation ascending
    order = np.argsort(elevations)
    for s in order:
        s = int(s)
        if field in radar.fields:
            sweep_slice = radar.get_slice(s)
            data = radar.fields[field]["data"][sweep_slice]
            if hasattr(data, "mask"):
                if not data.mask.all():
                    return s
            else:
                return s
    return 0


def render_radar_panel(
    level2_path: Path,
    output_path: Path,
    center_lat: float | None = None,
    center_lon: float | None = None,
    zoom_km: float = 60.0,
) -> Path:
    """Render a side-by-side reflectivity + velocity panel.

    If center_lat/lon are provided, the plot is zoomed to a box around that
    point. Otherwise it's centered on the radar.
    """
    radar = pyart.io.read_nexrad_archive(str(level2_path))

    refl_sweep = _lowest_sweep_with_field(radar, "reflectivity")
    vel_sweep = _lowest_sweep_with_field(radar, "velocity")

    fig, (ax_refl, ax_vel) = plt.subplots(1, 2, figsize=(14, 7), dpi=110)
    display = pyart.graph.RadarDisplay(radar)

    # --- Reflectivity ---
    display.plot_ppi(
        "reflectivity",
        sweep=refl_sweep,
        ax=ax_refl,
        vmin=-20,
        vmax=75,
        cmap="NWSRef",
        colorbar_label="Reflectivity (dBZ)",
        title=f"Base Reflectivity ({radar.fixed_angle['data'][refl_sweep]:.1f}°)",
        axislabels=("E-W (km)", "N-S (km)"),
    )

    # --- Velocity ---
    display.plot_ppi(
        "velocity",
        sweep=vel_sweep,
        ax=ax_vel,
        vmin=-30,
        vmax=30,
        cmap="NWSVel",
        colorbar_label="Velocity (m/s)",
        title=f"Base Velocity ({radar.fixed_angle['data'][vel_sweep]:.1f}°)",
        axislabels=("E-W (km)", "N-S (km)"),
    )

    # --- Zoom (if a center is provided, convert lat/lon to km from radar) ---
    if center_lat is not None and center_lon is not None:
        radar_lat = float(radar.latitude["data"][0])
        radar_lon = float(radar.longitude["data"][0])
        # Approx conversion (good enough at small scales)
        dy_km = (center_lat - radar_lat) * 111.0
        dx_km = (center_lon - radar_lon) * 111.0 * np.cos(np.radians(radar_lat))
        for ax in (ax_refl, ax_vel):
            ax.set_xlim(dx_km - zoom_km, dx_km + zoom_km)
            ax.set_ylim(dy_km - zoom_km, dy_km + zoom_km)
            ax.set_aspect("equal")
    else:
        for ax in (ax_refl, ax_vel):
            ax.set_xlim(-zoom_km, zoom_km)
            ax.set_ylim(-zoom_km, zoom_km)
            ax.set_aspect("equal")

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, bbox_inches="tight", dpi=110)
    plt.close(fig)
    return output_path