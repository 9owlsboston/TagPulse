"""Configuration for the edge agent.

All knobs are kept here so a deployed Pi can be reconfigured by pushing a new
`device.configuration` JSON from the server (see backlog item G8 / A12).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import UUID


@dataclass
class EdgeConfig:
    """Runtime configuration for `EdgeAgent`.

    Required fields are positional / no default; everything else has a sane
    default that matches the contract in
    `docs/design/asset-tracking-gap-analysis.md` §A5.
    """

    # -- Identity --
    tenant_id: UUID
    device_id: UUID

    # -- Transport --
    broker_host: str = "localhost"
    broker_port: int = 1883
    username: str | None = None
    password: str | None = None
    use_tls: bool = False
    tls_ca_path: str | None = None
    tls_cert_path: str | None = None  # client cert (mTLS / A6 phase 2)
    tls_key_path: str | None = None
    keepalive_s: int = 30

    # -- De-dup / ENTER-EXIT (A5) --
    dedup_window_s: float = 5.0
    exit_timeout_s: float = 10.0

    # -- Batching --
    batch_max_events: int = 100
    batch_max_age_s: float = 1.0

    # -- Offline buffer --
    buffer_path: str = "/var/lib/tagpulse/edge.sqlite"
    buffer_max_rows: int = 100_000
    buffer_max_age_s: float = 24 * 3600

    # -- Time hygiene (server will reject; we drop locally too) --
    max_event_age_s: float = 24 * 3600
    max_event_skew_future_s: float = 5 * 60

    # -- Heartbeat --
    heartbeat_period_s: float = 60.0

    # -- Reconnect backoff (full-jitter) --
    reconnect_initial_s: float = 1.0
    reconnect_max_s: float = 60.0

    # -- Identity for status messages --
    firmware_version: str = "unknown"

    # -- Topic templates --
    # Match the backend taxonomy in §A7. {t} = tenant_id, {d} = device_id.
    topic_tag_reads: str = "tenants/{t}/devices/{d}/tag-reads"
    topic_telemetry: str = "tenants/{t}/devices/{d}/telemetry"
    topic_location: str = "tenants/{t}/devices/{d}/location"
    topic_status: str = "tenants/{t}/devices/{d}/status"
    topic_events: str = "tenants/{t}/devices/{d}/events"

    # -- Logging --
    log_level: str = field(default_factory=lambda: os.environ.get("TAGPULSE_LOG", "INFO"))

    # -- Helpers --

    def topic(self, kind: str) -> str:
        attr = f"topic_{kind.replace('-', '_')}"
        try:
            template: str = getattr(self, attr)
        except AttributeError as exc:
            raise ValueError(f"Unknown topic kind: {kind}") from exc
        return template.format(t=self.tenant_id, d=self.device_id)

    @classmethod
    def from_json(cls, path: str | Path) -> EdgeConfig:
        """Load config from a JSON file. UUIDs may be strings."""
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        raw["tenant_id"] = UUID(str(raw["tenant_id"]))
        raw["device_id"] = UUID(str(raw["device_id"]))
        return cls(**raw)

    def to_dict(self) -> dict[str, object]:
        d = asdict(self)
        d["tenant_id"] = str(self.tenant_id)
        d["device_id"] = str(self.device_id)
        return d
