document.addEventListener('DOMContentLoaded', () => {
    const container = document.getElementById('event-map');
    if (!container) return;

    // Data
    const warnings = JSON.parse(container.dataset.warnings || '[]');
    const lsrs = JSON.parse(container.dataset.lsrs || '[]');
    const ncei = JSON.parse(container.dataset.ncei || '[]');

    const map = L.map('event-map', {
        zoomControl: true,
        fullscreenControl: true, // Appends the button to the map
        fullscreenControlOptions: {
            position: 'topright'
        }
    }).setView([39.0, -98.0], 9);

    L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
        attribution: '© OpenStreetMap © CARTO',
        maxZoom: 19,
        minZoom: 6.5,
        subdomains: 'abcd',
    }).addTo(map);

    const allPoints = [];

    // Warning polygons - handles both Polygon and MultiPolygon
    warnings.forEach(w => {
        if (!w.polygon) return;
        const isTornado = w.phenomena === 'TO';
        const color = isTornado ? '#dc2626' : '#facc15';

        const layer = L.geoJSON(w.polygon, {
            style: {
                color: color,
                weight: 2,
                fillColor: color,
                fillOpacity: 0.15,
            }
        }).bindPopup(`
            <b>${w.type || (isTornado ? 'Tornado Warning' : 'Severe Thunderstorm Warning')}</b><br>
            WFO: ${w.wfo || '-'}<br>
            Issued: ${w.issued_at ? w.issued_at.slice(0, 16).replace('T', ' ') + 'Z' : '-'}<br>
            Expired: ${w.expires_at ? w.expires_at.slice(0, 16).replace('T', ' ') + 'Z' : '-'}<br>
            ${w.locations ? `<i>${w.locations}</i>` : ''}
        `).addTo(map);

        // Collect bounds points
        if (w.polygon.type === 'MultiPolygon') {
            w.polygon.coordinates.forEach(poly =>
                poly[0].forEach(([lon, lat]) => allPoints.push([lat, lon]))
            );
        } else if (w.polygon.type === 'Polygon') {
            w.polygon.coordinates[0].forEach(([lon, lat]) => allPoints.push([lat, lon]));
        }
    });

    // LSR points
    const lsrColors = {
        'TORNADO': '#dc2626',
        'HAIL': '#3b82f6',
        'TSTM WND GST': '#f97316',
        'TSTM WND DMG': '#f97316',
        'FUNNEL CLOUD': '#a855f7',
    };

    lsrs.forEach(lsr => {
        if (!lsr.lat || !lsr.lon) return;
        const color = lsrColors[lsr.event] || '#94a3b8';
        L.circleMarker([lsr.lat, lsr.lon], {
            radius: 6,
            fillColor: color,
            color: '#fff',
            weight: 1,
            fillOpacity: 0.9,
        }).bindPopup(`
            <b>${lsr.event || 'LSR'}</b><br>
            ${lsr.magnitude ? `Magnitude: ${lsr.magnitude}<br>` : ''}
            ${lsr.city ? `Location: ${lsr.city}<br>` : ''}
            Time: ${lsr.time ? lsr.time.slice(0, 19) : '-'}<br>
            ${lsr.remarks ? `<i>${lsr.remarks}</i>` : ''}
        `).addTo(map);
        allPoints.push([lsr.lat, lsr.lon]);
    });

    // NCEI tornado tracks
    ncei.forEach(e => {
        if (e.event_type !== 'Tornado') return;
        if (!e.begin_lat || !e.begin_lon || !e.end_lat || !e.end_lon) return;

        const beginLat = parseFloat(e.begin_lat);
        const beginLon = parseFloat(e.begin_lon);
        const endLat = parseFloat(e.end_lat);
        const endLon = parseFloat(e.end_lon);
        const widthYards = parseFloat(e.tor_width_yd) || 0;
        

        // Color by EF scale
        const efColors = {
            'EF0': '#15fae7', 'EF1': '#35c709', 'EF2': '#eea942',
            'EF3': '#b91c1c', 'EF4': '#3b0303', 'EF5': '#3b0a45',
            'EFU': '#94a3b8',
        };
        const color = efColors[e.tor_f_scale] || '#7f1d1d';



        // width buffer

        const midLat = (beginLat + endLat) / 2;
        const widthMeters = widthYards * 0.9144; // Convert yards to meters
        const halfWidthDegLat = (widthMeters / 111320) / 2; // Approximate conversion to degrees latitude
        const halfWidthDegLon = halfWidthDegLat / Math.cos(midLat * Math.PI / 180); // Adjust for longitude
        const angle = Math.atan2(endLat - beginLat, endLon - beginLon);
        const perpLat = Math.cos(angle) * halfWidthDegLat;
        const perpLon = Math.sin(angle) * halfWidthDegLon;

        const corridor = [
            [beginLat + perpLat, beginLon - perpLon],
            [endLat + perpLat, endLon - perpLon],
            [endLat - perpLat, endLon + perpLon],
            [beginLat - perpLat, beginLon + perpLon],
        ]

        L.polygon(corridor, {
            color: 'transparent',
            fillColor: color,
            fillOpacity: 0.3,
            weight: 0,
        }).addTo(map);


        const weight = e.tor_f_scale === 'EF5' ? 6 : e.tor_f_scale === 'EF4' ? 5 : 3;

        L.polyline([[beginLat, beginLon], [endLat, endLon]], {
            color: color,
            weight: weight,
            opacity: 0.9,
        }).bindPopup(`
            <b>${e.tor_f_scale || 'Tornado'} - ${e.county} County</b><br>
            ${e.begin_time ? `Time: ${e.begin_time}<br>` : ''}
            ${e.tor_length_mi ? `Path: ${e.tor_length_mi} mi<br>` : ''}
            ${e.tor_width_yd ? `Width: ${e.tor_width_yd} yd<br>` : ''}
            ${(e.deaths_direct + e.deaths_indirect) > 0 ? `Deaths: ${e.deaths_direct + e.deaths_indirect}<br>` : ''}
            ${(e.injuries_direct + e.injuries_indirect) > 0 ? `Injuries: ${e.injuries_direct + e.injuries_indirect}<br>` : ''}
            ${e.damage_property ? `Damage: $${Number(e.damage_property).toLocaleString()}<br>` : ''}
        `).addTo(map);

        allPoints.push([beginLat, beginLon], [endLat, endLon]);
    });

    let finalBounds = null;

    // Fit bounds to all features
    if (allPoints.length > 0) {

        const calculatedBounds = L.latLngBounds(allPoints).pad(0.1);
        finalBounds = calculatedBounds;

        map.setMaxBounds(finalBounds);

        map.fitBounds(finalBounds);
    }
    else {
        console.warn("No points found to calculate bounds")
    }

    // Allow the map to expand completely when going into fullscreen mode
    map.on('fullscreenchange', () => {
        if (map.isFullscreen()) {
            // Remove limits so the map can stretch to fill the screen dimensions
            map.setMaxBounds(null);
        } else if (finalBounds) {
            // Re-apply original restrictions when exiting fullscreen
            map.setMaxBounds(finalBounds);
            map.fitBounds(finalBounds);
        }
    });

});