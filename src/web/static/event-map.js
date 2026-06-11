document.addEventListener('DOMContentLoaded', () => {
    const container = document.getElementById('event-map');
    if (!container) return;

    // Data
    const warnings = JSON.parse(container.dataset.warnings || '[]');
    const lsrs = JSON.parse(container.dataset.lsrs || '[]');
    const ncei = JSON.parse(container.dataset.ncei || '[]');
    const datTracks = JSON.parse(container.dataset.datTracks || '{"polygons":[],"lines":[]}');
    const spcOutlook = JSON.parse(container.dataset.spcOutlook || 'null');

    // Override DAT ef_scale with comments if they mention a higher rating
    function parseEFFromComments(efScale, comments) {
        if (!comments) return efScale;
        const matches = [...comments.matchAll(/EF[-\s]?([0-5])/gi)];
        if (!matches.length) return efScale;
        const efRank = { 'EF0': 0, 'EF1': 1, 'EF2': 2, 'EF3': 3, 'EF4': 4, 'EF5': 5 };
        const highest = matches.reduce((best, m) => {
            const candidate = `EF${m[1]}`;
            return (efRank[candidate] || 0) > (efRank[best] || 0) ? candidate : best;
        }, efScale);
        return highest;
    }

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
        minZoom: 6,
        subdomains: 'abcd',
    }).addTo(map);

    const allPoints = [];

    if (spcOutlook && spcOutlook.features) {
        spcOutlook.features.forEach(f => {
            const props = f.properties;
            if (props.LABEL === 'TSTM') return; // Dont need basic thunderstorm area
            L.geoJSON(f, {
                style: {
                    color: props.stroke || "#999",
                    weight: 1.5,
                    fillColor: props.fill || "#ccc",
                    fillOpacity: 0.25,
                }
            }).bindPopup(`
                <b>SPC Day 1 Outlook: ${props.LABEL}</b><br>
                ${props.LABEL2}<br>
                <small>Issued: ${props.ISSUE ? props.ISSUE.slice(0, 8) + ' ' + props.ISSUE.slice(8, 12) + 'Z' : '-'}</small>
                `).addTo(map);
        })

        const eventYear = parseInt(container.dataset.eventYear || '2015'); // default to 2015 - Modern scheme
        try{
            console.log("Event Year: ", eventYear);
        }catch{
            console.log("Failed");
        }
        const modernScheme = eventYear >= 2014;

        const legend = L.control({ position: 'bottomleft' });
        legend.onAdd = () => {
            const div = L.DomUtil.create('div');
            div.style.cssText = 'background:rgba(255,255,255,0.9);padding:8px 10px;font-size:11px;line-height:1.6;border:1px solid #ccc;';
            div.innerHTML = `
        <div style="font-weight:bold;margin-bottom:4px;font-family:Oswald,sans-serif;font-size:12px;">SPC DAY 1 OUTLOOK</div>
        <div><span style="display:inline-block;width:12px;height:12px;background:#FF00FF;margin-right:5px;"></span>HIGH</div>
        <div><span style="display:inline-block;width:12px;height:12px;background:#E06666;margin-right:5px;"></span>MDT</div>
        ${modernScheme ? `<div><span style="display:inline-block;width:12px;height:12px;background:#FFA366;margin-right:5px;"></span>ENH</div>` : ''}
        <div><span style="display:inline-block;width:12px;height:12px;background:#FFE066;margin-right:5px;"></span>SLGT</div>
        ${modernScheme ? `<div><span style="display:inline-block;width:12px;height:12px;background:#66A366;margin-right:5px;"></span>MRGL</div>` : ''}
    `;
            return div;
        };
        legend.addTo(map);
    }

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

    const efColors = {
        'EF0': '#15fae7', 'EF1': '#35c709', 'EF2': '#eea942',
        'EF3': '#b91c1c', 'EF4': '#3b0303', 'EF5': '#3b0a45',
        'EFU': '#94a3b8',
    };

    const hasDat = (datTracks.polygons.length + datTracks.lines.length) > 0;

    if (hasDat) {
        // DAT polygons - variable-width damage corridors (Joplin, El Reno etc)
        datTracks.polygons.forEach(t => {
            if (!t.coords || t.coords.length < 3) return;
            const efScale = parseEFFromComments(t.ef_scale, t.comments);
            const color = efColors[efScale] || '#7f1d1d';
            L.polygon(t.coords, {
                color: color,
                weight: 1.5,
                fillColor: color,
                fillOpacity: 0.35,
            }).bindPopup(`
        <b>${efScale || 'Tornado'} - DAT Damage Polygon</b><br>
        ${t.length_mi && t.length_mi > 0 ? `Path: ${t.length_mi} mi<br>` : ''}
        ${t.width_yd && t.width_yd > 0 ? `Width: ${t.width_yd} yd<br>` : ''}
        ${t.fatalities > 0 ? `Fatalities: ${t.fatalities}<br>` : ''}
        ${t.injuries > 0 ? `Injuries: ${t.injuries}<br>` : ''}
        ${t.comments ? `<em>${t.comments}</em>` : ''}
    `).addTo(map);
            t.coords.forEach(c => allPoints.push(c));
        });

        // DAT lines - curved multi-point surveyed centerlines
        datTracks.lines.forEach(t => {
            if (!t.coords || t.coords.length < 2) return;
            const efScale = parseEFFromComments(t.ef_scale, t.comments);
            const color = efColors[efScale] || '#7f1d1d';
            const weight = efScale === 'EF5' ? 6 : efScale === 'EF4' ? 5 : 3;

            // Width buffer along the surveyed line
            if (t.width_yd && t.width_yd > 0 && t.coords.length >= 2) {
                // Use Leaflet's built-in weight scaling as a proxy for width
                L.polyline(t.coords, {
                    color: color,
                    weight: Math.max(weight, Math.min(30, t.width_yd / 80)),
                    opacity: 0.2,
                }).addTo(map);
            }

            L.polyline(t.coords, {
                color: color,
                weight: weight,
                opacity: 0.9,
            }).bindPopup(`
                <b>${t.ef_scale || 'Tornado'} - DAT Surveyed Track</b><br>
                ${t.event_id ? `Event: ${t.event_id}<br>` : ''}
                ${t.length_mi && t.length_mi > 0 ? `Path: ${t.length_mi} mi<br>` : ''}
                ${t.width_yd && t.width_yd > 0 ? `Width: ${t.width_yd} yd<br>` : ''}
                ${t.max_wind && t.max_wind > 0 ? `Max Wind: ${t.max_wind} mph<br>` : ''}
                ${t.fatalities > 0 ? `Fatalities: ${t.fatalities}<br>` : ''}
                ${t.injuries > 0 ? `Injuries: ${t.injuries}<br>` : ''}
                ${t.wfo ? `WFO: ${t.wfo}<br>` : ''}
            `).addTo(map);

            t.coords.forEach(c => allPoints.push(c));
        });

    } else {
        // Fallback - NCEI straight lines
        ncei.forEach(e => {
            if (e.event_type !== 'Tornado') return;
            if (!e.begin_lat || !e.begin_lon || !e.end_lat || !e.end_lon) return;

            const beginLat = parseFloat(e.begin_lat);
            const beginLon = parseFloat(e.begin_lon);
            const endLat = parseFloat(e.end_lat);
            const endLon = parseFloat(e.end_lon);
            const widthYards = parseFloat(e.tor_width_yd) || 0;
            const color = efColors[e.tor_f_scale] || '#7f1d1d';

            const midLat = (beginLat + endLat) / 2;
            const widthMeters = widthYards * 0.9144;
            const halfWidthDegLat = (widthMeters / 111320) / 2;
            const halfWidthDegLon = halfWidthDegLat / Math.cos(midLat * Math.PI / 180);
            const angle = Math.atan2(endLat - beginLat, endLon - beginLon);
            const perpLat = Math.cos(angle) * halfWidthDegLat;
            const perpLon = Math.sin(angle) * halfWidthDegLon;

            const corridor = [
                [beginLat + perpLat, beginLon - perpLon],
                [endLat + perpLat, endLon - perpLon],
                [endLat - perpLat, endLon + perpLon],
                [beginLat - perpLat, beginLon + perpLon],
            ];

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
    }

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