"""TagPulse edge client — reference implementation for Raspberry Pi readers.

Public API:
    EdgeAgent             — v1 orchestrator; start/stop, submit_*
    EdgeConfig            — runtime configuration (loadable from JSON or env)
    RawTagRead            — input event from the hardware reader loop
    SensorSample          — input event for sensor-only telemetry
    LocationFix           — input event for GPS / location updates
    WmV2Producer          — v2 wire-format producer (Sprint 47, ADR-025)
    CycleEpcObservation   — v2 producer per-cycle input record
"""

from tagpulse_edge.agent import EdgeAgent
from tagpulse_edge.config import EdgeConfig
from tagpulse_edge.events import LocationFix, RawTagRead, SensorSample
from tagpulse_edge.wm_v2_producer import CycleEpcObservation, WmV2Producer

__all__ = [
    "CycleEpcObservation",
    "EdgeAgent",
    "EdgeConfig",
    "LocationFix",
    "RawTagRead",
    "SensorSample",
    "WmV2Producer",
]

__version__ = "0.1.0"
