"""Feature Availablity Gating based on Event Date

NWS / NEXRAD products were deployed over a period of several years, mainly in the 1990s.
This module is used to help the pipeline know whats available for a given event date,
so that it can either run, or skip each step in a more graceful way.
"""
from dataclasses import dataclass, field
from datetime import date, datetime

SBW_POLYGONS_DEPLOYED = date(2007, 10, 1)
DUAL_POL_DEPLOYED = date(2013, 4, 1)
IEM_LSR_RELIABLE = date(2002, 1, 1)
VTEC_WARNINGS_START = date(1996, 1, 1)




@dataclass
class FeatureAvailability:
    """ What data products are available for a given event data and radar """
    radar: bool = False
    radar_dual_pol: bool = False
    sbw_polygons: bool = False
    vtec_warnings: bool = False
    iem_lsr: bool = False
    notes: list[str] = field(default_factory=list)
    
    
def feature_availability(
    event_date: date | datetime,
    radar_commissioned: date | str | None = None,
) -> FeatureAvailability:
    """Return what data products should be attempted for this event data
    
    Args:
        event_date: the date of the event to be reported
        radar_commissioned: When the relevant NEXRAD radar came online
    """
    
    if isinstance(event_date, datetime):
        event_date = event_date.date()
        
    if isinstance(radar_commissioned, str):
        radar_commissioned = date.fromisoformat(radar_commissioned)
        
    avail = FeatureAvailability()
    
    # check for radar availability
    if radar_commissioned is None:
        avail.notes.append("No radar station selected, or radar station unknown")
    elif event_date >= radar_commissioned:
        avail.radar = True
    else:
        avail.notes.append(
            f"Event predates radar commissioning ({radar_commissioned.isoformat()}); "
            "radar data unavailable for this site."
        )
        
    if event_date >= VTEC_WARNINGS_START:
        avail.vtec_warnings = True
    else:
        avail.notes.append(
            f"Event predates IEM VTEC archive ({VTEC_WARNINGS_START}); warning data unavailable."
        )
        
    # Check for dual-pol availability
    if event_date >= DUAL_POL_DEPLOYED:
        avail.radar_dual_pol = True
    elif avail.radar:
        avail.notes.append(
            f"Event predates dual-polarization radar deployment ({DUAL_POL_DEPLOYED.isoformat()}); "
            "CC/ZDR products not available."
        )
        
    # Check for SBW polygon availability
    if event_date >= SBW_POLYGONS_DEPLOYED:
        avail.sbw_polygons = True
    else:
        avail.notes.append(
            f"Event predates Storm-Based Warning polygons ({SBW_POLYGONS_DEPLOYED.isoformat()}); "
            "warning polygons may be less accurate."
        )
        
    # Check for IEM LSR reliability
    if event_date >= IEM_LSR_RELIABLE:
        avail.iem_lsr = True
    else:
        avail.notes.append(
            f"Event predates reliable IEM LSR data ({IEM_LSR_RELIABLE.isoformat()}); "
            "LSR data may be incomplete."
        )
        
    return avail