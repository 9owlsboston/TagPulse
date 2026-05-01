"""TagPulse edge client — reference implementation for Raspberry Pi readers.

Public API:
    EdgeAgent       — orchestrator; start/stop, submit_*
    EdgeConfig      — runtime configuration (loadable from JSON or env)
    RawTagRead      — input event from the hardware reader loop
    SensorSample    — input event for sensor-only telemetry
    LocationFix     — input event for GPS / location updates
"""

from tagpulse_edge.agent import EdgeAgent
from tagpulse_edge.config import EdgeConfig
from tagpulse_edge.events import LocationFix, RawTagRead, SensorSample

__all__ = [
    "EdgeAgent",
    "EdgeConfig",
    "LocationFix",
    "RawTagRead",
    "SensorSample",
]

__version__ = "0.1.0"
