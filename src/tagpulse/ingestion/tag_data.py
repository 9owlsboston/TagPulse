"""Helpers for shaping tag-borne data payloads (Sprint 14).

Currently provides:
- ``cap_tag_data``: enforces the 4 KB inline cap on ``tag_reads.tag_data``.
"""

from __future__ import annotations

import json
from typing import Any

from tagpulse.core.otel_metrics import tag_data_truncations_counter

# Hard inline cap per docs/design/rfid-tag-data-model.md §9 Q2.
TAG_DATA_MAX_BYTES = 4096


def cap_tag_data(
    payload: dict[str, Any] | None,
    *,
    tenant_id: str | None = None,
) -> dict[str, Any] | None:
    """Return ``payload`` unchanged if under the inline cap, else a truncated copy.

    Strategy when oversized: drop keys in iteration order until the JSON
    representation fits, then add ``_truncated=true`` so consumers can
    tell the blob is incomplete. Increments a tenant-scoped OTel counter.
    The full blob remains available in ``device_telemetry`` rows that the
    ingestion mirror writes per [rfid-tag-data-model.md §6].
    """
    if not payload:
        return payload
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(encoded) <= TAG_DATA_MAX_BYTES:
        return payload

    capped: dict[str, Any] = {}
    overhead = len(b'{"_truncated":true}')
    for key, value in payload.items():
        candidate = {**capped, key: value}
        size = len(json.dumps(candidate, separators=(",", ":")).encode("utf-8"))
        if size + overhead > TAG_DATA_MAX_BYTES:
            break
        capped[key] = value
    capped["_truncated"] = True

    attrs = {"tenant_id": tenant_id} if tenant_id else {}
    tag_data_truncations_counter.add(1, attrs)
    return capped
