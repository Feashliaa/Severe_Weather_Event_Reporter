"""Shared data models used across modules."""
from dataclasses import dataclass


@dataclass
class VTECEventRef:
    """Identifies a specific NWS VTEC warning in the IEM archive."""
    wfo: str
    year: int
    phenomena: str
    significance: str
    etn: int