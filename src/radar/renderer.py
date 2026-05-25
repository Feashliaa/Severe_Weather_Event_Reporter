"""Render Level II radar data to PNG images for the report."""
import math
import numpy as np
import pyart
import cartopy.crs as ccrs
import cartopy.feature as cfeature
import cartopy.io.shapereader as shpreader
import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt

from pathlib import Path
from matplotlib import patheffects

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two points."""
    R = 6371.0
    lat1r, lat2r = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1r) * math.cos(lat2r) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


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


def _build_map_extent(
    center_lat: float,
    center_lon: float,
    radar_lat: float,
    radar_lon: float,
    zoom_km: float,
) -> tuple[list[float], float, float, float]:
    """Compute map center and extent. Returns (extent, map_lat, map_lon, effective_zoom_km)."""
    dist_to_radar = _haversine_km(center_lat, center_lon, radar_lat, radar_lon)
    if dist_to_radar > zoom_km * 2.0:
        map_lat = (center_lat + radar_lat) / 2
        map_lon = (center_lon + radar_lon) / 2
        zoom_km = dist_to_radar * 0.7
    else:
        map_lat = center_lat
        map_lon = center_lon

    lat_deg = zoom_km / 111.0
    lon_deg = zoom_km / (111.0 * np.cos(np.radians(map_lat)))
    extent = [map_lon - lon_deg, map_lon + lon_deg, map_lat - lat_deg, map_lat + lat_deg]
    return extent, map_lat, map_lon, zoom_km


def _patch_radar_location(
    radar,
    radar_site_lat: float | None,
    center_lat: float | None,
    radar_site_lon: float | None,
    center_lon: float | None,
) -> tuple[float, float]:
    """Fix missing radar lat/lon in older V03 files. Returns (radar_lat, radar_lon)."""
    radar_lat = float(radar.latitude["data"][0])
    radar_lon = float(radar.longitude["data"][0])
    if abs(radar_lat) < 1.0 and abs(radar_lon) < 1.0:
        radar_lat = radar_site_lat or center_lat or 37.0
        radar_lon = radar_site_lon or center_lon or -97.0
        radar.latitude["data"][0] = radar_lat
        radar.longitude["data"][0] = radar_lon
        radar.altitude["data"][0] = 100.0
    return radar_lat, radar_lon


def _build_figure(has_dual_pol: bool, proj):
    """Create the matplotlib figure and axes. Returns (fig, panel_axes, named_axes)."""
    if has_dual_pol:
        fig, axes = plt.subplots(2, 2, figsize=(16, 14), dpi=110, subplot_kw={"projection": proj})
        ax_refl, ax_vel = axes[0, 0], axes[0, 1]
        ax_cc, ax_sw = axes[1, 0], axes[1, 1]
        return fig, [ax_refl, ax_vel, ax_cc, ax_sw], (ax_refl, ax_vel, ax_cc, ax_sw)
    else:
        fig, (ax_refl, ax_vel) = plt.subplots(1, 2, figsize=(14, 7), dpi=110, subplot_kw={"projection": proj})
        return fig, [ax_refl, ax_vel], (ax_refl, ax_vel, None, None)


def _add_map_features(ax, extent: list[float], counties, states, city_records: list) -> None:
    """Add geographic overlays to a single axes."""
    ax.add_feature(counties)
    ax.add_feature(states)
    for city in city_records:
        city_lon = float(city.geometry.x)  # type: ignore
        city_lat = float(city.geometry.y)  # type: ignore
        name = city.attributes.get("NAME", "")
        ax.plot(city_lon, city_lat, "k.", markersize=5, transform=ccrs.PlateCarree(), zorder=5)
        ax.text(
            city_lon, city_lat, f" {name}",
            fontsize=8, fontweight="bold",
            transform=ccrs.PlateCarree(),
            verticalalignment="center", zorder=5,
            path_effects=[patheffects.withStroke(linewidth=2.5, foreground="white")],
        )


def _load_city_records(extent: list[float]) -> list:
    """Load and filter populated places within the map extent."""
    cities_path = shpreader.natural_earth(resolution="10m", category="cultural", name="populated_places")
    reader = shpreader.Reader(cities_path)
    return [
        city for city in reader.records()
        if (extent[0] < float(city.geometry.x) < extent[1]  # type: ignore
            and extent[2] < float(city.geometry.y) < extent[3]  # type: ignore
            and int(city.attributes.get("POP_MAX", 0) or 0) > 1000)  # type: ignore
    ]


def render_radar_panel(
    level2_path: Path,
    output_path: Path,
    center_lat: float | None = None,
    center_lon: float | None = None,
    zoom_km: float = 50.0,
    radar_site_lat: float | None = None,
    radar_site_lon: float | None = None,
) -> Path:
    """Render a dual or quad-panel radar image with geographic overlay."""
    radar = pyart.io.read_nexrad_archive(str(level2_path))
    refl_sweep = _lowest_sweep_with_field(radar, "reflectivity")
    vel_sweep = _lowest_sweep_with_field(radar, "velocity")

    radar_lat, radar_lon = _patch_radar_location(
        radar, radar_site_lat, center_lat, radar_site_lon, center_lon
    )

    if center_lat is not None and center_lon is not None:
        extent, map_lat, map_lon, zoom_km = _build_map_extent(
            center_lat, center_lon, radar_lat, radar_lon, zoom_km
        )
    else:
        extent, map_lat, map_lon, zoom_km = _build_map_extent(
            radar_lat, radar_lon, radar_lat, radar_lon, zoom_km
        )

    proj = ccrs.PlateCarree()
    has_dual_pol = (
        "cross_correlation_ratio" in radar.fields
        and "spectrum_width" in radar.fields
    )

    fig, panel_axes, (ax_refl, ax_vel, ax_cc, ax_sw) = _build_figure(has_dual_pol, proj)
    display = pyart.graph.RadarMapDisplay(radar)

    ppi_kwargs = dict(
        projection=proj,
        min_lat=extent[2], max_lat=extent[3],
        min_lon=extent[0], max_lon=extent[1],
    )

    display.plot_ppi_map("reflectivity", sweep=refl_sweep, ax=ax_refl,
        vmin=-20, vmax=75, cmap="NWSRef", colorbar_label="Reflectivity (dBZ)",
        title=f"Base Reflectivity ({radar.fixed_angle['data'][refl_sweep]:.1f}°)", **ppi_kwargs) # type: ignore[arg-type]

    display.plot_ppi_map("velocity", sweep=vel_sweep, ax=ax_vel,
        vmin=-30, vmax=30, cmap="NWSVel", colorbar_label="Velocity (m/s)",
        title=f"Base Velocity ({radar.fixed_angle['data'][vel_sweep]:.1f}°)", **ppi_kwargs) # type: ignore[arg-type]

    if has_dual_pol and ax_cc is not None and ax_sw is not None:
        cc_sweep = _lowest_sweep_with_field(radar, "cross_correlation_ratio")
        sw_sweep = _lowest_sweep_with_field(radar, "spectrum_width")

        display.plot_ppi_map("cross_correlation_ratio", sweep=cc_sweep, ax=ax_cc,
            vmin=0.2, vmax=1.05, cmap="NWS_CC", colorbar_label="Correlation Coefficient",
            title=f"Correlation Coefficient ({radar.fixed_angle['data'][cc_sweep]:.1f}°)", **ppi_kwargs) # type: ignore[arg-type]

        display.plot_ppi_map("spectrum_width", sweep=sw_sweep, ax=ax_sw,
            vmin=0, vmax=10, cmap="NWS_SPW", colorbar_label="Spectrum Width (m/s)",
            title=f"Spectrum Width ({radar.fixed_angle['data'][sw_sweep]:.1f}°)", **ppi_kwargs) # type: ignore[arg-type]

    counties = cfeature.NaturalEarthFeature(
        category="cultural", name="admin_2_counties",
        scale="10m", facecolor="none", edgecolor="grey", linewidth=0.5,
    )
    states = cfeature.NaturalEarthFeature(
        category="cultural", name="admin_1_states_provinces_lines",
        scale="10m", facecolor="none", edgecolor="black", linewidth=0.8,
    )
    city_records = _load_city_records(extent)

    for ax in panel_axes:
        _add_map_features(ax, extent, counties, states, city_records)

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, bbox_inches="tight", dpi=110)
    plt.close(fig)
    return output_path