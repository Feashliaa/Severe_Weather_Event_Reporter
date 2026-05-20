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


def render_radar_panel(
    level2_path: Path,
    output_path: Path,
    center_lat: float | None = None,
    center_lon: float | None = None,
    zoom_km: float = 60.0,
    radar_site_lat: float | None = None,
    radar_site_lon: float | None = None,
) -> Path:
    """Render a side-by-side reflectivity + velocity panel.

    If center_lat/lon are provided, the plot is zoomed to a box around that
    point. Otherwise it's centered on the radar.
    """
    radar = pyart.io.read_nexrad_archive(str(level2_path))
    
    refl_sweep = _lowest_sweep_with_field(radar, "reflectivity")
    vel_sweep = _lowest_sweep_with_field(radar, "velocity")
    
    radar_lat = float(radar.latitude["data"][0])
    radar_lon = float(radar.longitude["data"][0])

    if abs(radar_lat) < 1.0 and abs(radar_lon) < 1.0:
        # Use known station coords if available, otherwise fall back to event center
        fallback_lat = radar_site_lat if radar_site_lat is not None else center_lat or 37.0
        fallback_lon = radar_site_lon if radar_site_lon is not None else center_lon or -97.0
        radar_lat = fallback_lat
        radar_lon = fallback_lon
        radar.latitude["data"][0] = radar_lat
        radar.longitude["data"][0] = radar_lon
        radar.altitude["data"][0] = 100.0
    
    if center_lat is not None and center_lon is not None:
        dist_to_radar = _haversine_km(center_lat, center_lon, radar_lat, radar_lon)
        if dist_to_radar > zoom_km * 0.8:
            map_lat = (center_lat + radar_lat) / 2
            map_lon = (center_lon + radar_lon) / 2
            zoom_km = dist_to_radar * 0.7
        else:
            map_lat = radar_lat
            map_lon = radar_lon
    else:
        map_lat = radar_lat
        map_lon = radar_lon
        
    lat_deg = zoom_km / 111.0
    lon_deg = zoom_km / (111.0 * np.cos(np.radians(map_lat)))
    
    extent = [
        map_lon - lon_deg,
        map_lon + lon_deg,
        map_lat - lat_deg,
        map_lat + lat_deg,
    ]
    
    proj = ccrs.PlateCarree()

    fig, (ax_refl, ax_vel) = plt.subplots(
        1, 2, 
        figsize=(14, 7), 
        dpi=110,
        subplot_kw={"projection": proj}
    )

    display = pyart.graph.RadarMapDisplay(radar)
    
    has_dual_pol = (
        "cross_correlation_ratio" in radar.fields
        and "spectrum_width" in radar.fields
    )

    if has_dual_pol:
        fig, axes = plt.subplots(
            2, 2,
            figsize=(16, 14),
            dpi=110,
            subplot_kw={"projection": proj},
        )
        ax_refl = axes[0, 0]
        ax_vel  = axes[0, 1]
        ax_cc   = axes[1, 0]
        ax_sw   = axes[1, 1]
        panel_axes = [ax_refl, ax_vel, ax_cc, ax_sw]
    else:
        fig, (ax_refl, ax_vel) = plt.subplots(
            1, 2,
            figsize=(14, 7),
            dpi=110,
            subplot_kw={"projection": proj},
        )
        panel_axes = [ax_refl, ax_vel]

    # --- Reflectivity ---
    display.plot_ppi_map(
        "reflectivity",
        sweep=refl_sweep,
        ax=ax_refl,
        vmin=-20,
        vmax=75,
        cmap="NWSRef",
        colorbar_label="Reflectivity (dBZ)",
        title=f"Base Reflectivity ({radar.fixed_angle['data'][refl_sweep]:.1f}°)",
        projection=proj,
        min_lat=extent[2],
        max_lat=extent[3],
        min_lon=extent[0],
        max_lon=extent[1],
    )

    # --- Velocity ---
    display.plot_ppi_map(
        "velocity",
        sweep=vel_sweep,
        ax=ax_vel,
        vmin=-30,
        vmax=30,
        cmap="NWSVel",
        colorbar_label="Velocity (m/s)",
        title=f"Base Velocity ({radar.fixed_angle['data'][vel_sweep]:.1f}°)",
        projection=proj,
        min_lat=extent[2],
        max_lat=extent[3],
        min_lon=extent[0],
        max_lon=extent[1],
    )
    
    if has_dual_pol:
            cc_sweep = _lowest_sweep_with_field(radar, "cross_correlation_ratio")
            sw_sweep = _lowest_sweep_with_field(radar, "spectrum_width")

            # --- Correlation Coefficient ---
            display.plot_ppi_map(
                "cross_correlation_ratio",
                sweep=cc_sweep,
                ax=ax_cc,
                vmin=0.2, vmax=1.05,
                cmap="NWS_CC",
                colorbar_label="Correlation Coefficient",
                title=f"Correlation Coefficient ({radar.fixed_angle['data'][cc_sweep]:.1f}°)",
                projection=proj,
                min_lat=extent[2],
                max_lat=extent[3],
                min_lon=extent[0],
                max_lon=extent[1],
            )

            # --- Spectrum Width ---
            display.plot_ppi_map(
                "spectrum_width",
                sweep=sw_sweep,
                ax=ax_sw,
                vmin=0, vmax=10,
                cmap="NWS_SPW",
                colorbar_label="Spectrum Width (m/s)",
                title=f"Spectrum Width ({radar.fixed_angle['data'][sw_sweep]:.1f}°)",
                projection=proj,
                min_lat=extent[2],
                max_lat=extent[3],
                min_lon=extent[0],
                max_lon=extent[1],
            )

    # --- Map features on both panels ---
    counties = cfeature.NaturalEarthFeature(
        category="cultural",
        name="admin_2_counties",
        scale="10m",
        facecolor="none",
        edgecolor="grey",
        linewidth=0.5,
    )
    states = cfeature.NaturalEarthFeature(
        category="cultural",
        name="admin_1_states_provinces_lines",
        scale="10m",
        facecolor="none",
        edgecolor="black",
        linewidth=0.8,
    )

    for ax in (ax_refl, ax_vel):
        
        ax.add_feature(counties)
        ax.add_feature(states)
        
        cities = shpreader.natural_earth(
            resolution="10m",
            category="cultural",
            name="populated_places",
        )
        reader = shpreader.Reader(cities)
        for city in reader.records():
            city_lon = city.geometry.x # type: ignore
            city_lat = city.geometry.y # type: ignore
            # Only label cities within the extent
            if (extent[0] < city_lon < extent[1] and
                    extent[2] < city_lat < extent[3]):
                pop = city.attributes.get("POP_MAX", 0)
                name = city.attributes.get("NAME", "")
                # Only label places with population > 1000
                if pop > 1000: # type: ignore
                    ax.plot(city_lon, city_lat, "k.", markersize=5,
                    transform=ccrs.PlateCarree(), zorder=5)
                    ax.text(
                        city_lon, city_lat, f" {name}",
                        fontsize=8,
                        fontweight="bold",
                        transform=ccrs.PlateCarree(),
                        verticalalignment="center",
                        zorder=5,
                        path_effects=[
                            patheffects.withStroke(linewidth=2.5, foreground="white")
                        ],
                    )

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, bbox_inches="tight", dpi=110)
    plt.close(fig)
    return output_path