"""Pydantic models for the WM v2 edge wire format (Sprint 46, ADR-025).

Discriminated union on the integer ``t`` field:

- ``t=0`` → :class:`WmSnapMessage` (snapshot, carries ``epcs[]``)
- ``t=1`` → :class:`WmAppearedMessage` (add — one EPC just appeared)
- ``t=2`` → :class:`WmDisappearedMessage` (sub — one EPC just departed)

The discriminated-union root :data:`WmMessage` is what the MQTT subscriber
parses against (see :mod:`tagpulse.ingestion.mqtt_subscriber`). Each
variant has ``extra="forbid"`` so reserved field names (``v``, ``hb``,
``err``, ``cfg``, ``seq`` — see spec §2.2) and shape-violations like
``epcs`` appearing on a ``t=1`` (spec §6 ``epcs_wrong_type``) are
rejected at parse time.

This module is **wire-shape validation only**. Identity (``sn``→``device_id``),
clock skew, JWT cross-checks, and reconciliation against ``tag_presence``
live in :mod:`tagpulse.ingestion.mqtt_subscriber` and the reconciler
module added in Phase C.

See :doc:`docs/design/edge-wire-format-v2.md` §2–§3 for the authoritative
wire specification and [ADR-025](../../docs/adr/025-edge-wire-format-v2.md)
for the design rationale.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------------------
# Field constants (spec §2.2)
# ---------------------------------------------------------------------------

EPC_MIN_HEX_CHARS = 8  # 32-bit EPC floor
EPC_MAX_HEX_CHARS = 124  # 496-bit EPC ceiling (spec §2.2)

ANTENNA_MIN = 0
ANTENNA_MAX = 255  # uint8, 0 = unknown/muxed

RSSI_MIN = -127  # int16 floor we actually use
RSSI_MAX = 0  # dBm — anything > 0 is malformed

CNT_MIN = 1
CNT_MAX = 65535  # uint16

LAT_MIN = -90.0
LAT_MAX = 90.0
LON_MIN = -180.0
LON_MAX = 180.0

TMP_MIN = -40.0
TMP_MAX = 85.0
HUM_MIN = 0.0
HUM_MAX = 100.0

# Spec §6 default soft cap on snapshot size — exceeding does NOT reject,
# only logs + counter. Kept here so the subscriber and the conformance
# tests share one number.
SNAP_SOFT_CAP_ENTRIES = 5000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_epc(value: str) -> str:
    """Validate an EPC hex string per spec §2.2.

    Uppercase hex, 8..124 chars, even length (whole bytes). No ``0x``
    prefix, no whitespace. Returns the canonical (uppercased) form.
    """
    if not isinstance(value, str):
        raise ValueError("epc must be a string")
    if len(value) < EPC_MIN_HEX_CHARS or len(value) > EPC_MAX_HEX_CHARS:
        raise ValueError(
            f"epc length {len(value)} outside {EPC_MIN_HEX_CHARS}..{EPC_MAX_HEX_CHARS}"
        )
    if len(value) % 2 != 0:
        raise ValueError("epc length must be even (whole bytes)")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError("epc must be hexadecimal") from exc
    return value.upper()


def _reject_explicit_null_optional(data: dict[str, Any], fields: tuple[str, ...]) -> None:
    """Reject explicit ``null`` on optional fields (spec §6 ``explicit_null``).

    Optional fields MUST be omitted entirely when absent — senders MUST
    NOT emit ``"key":null``. This is enforced in ``mode="before"`` so
    that ``None`` from the raw payload is distinguishable from an
    omitted key (Pydantic would otherwise coerce both to the default).
    """
    for name in fields:
        if name in data and data[name] is None:
            raise ValueError(
                f"explicit null on optional field {name!r} is forbidden (omit the key instead)"
            )


# ---------------------------------------------------------------------------
# Per-EPC entry (lives inside t=0 ``epcs[]``)
# ---------------------------------------------------------------------------


class WmSnapEntry(BaseModel):
    """One per-EPC observation inside a ``t=0`` snap's ``epcs[]`` array.

    Spec §2.2 "Per-EPC entry fields" table. All of ``an`` / ``epc`` /
    ``rssi`` / ``cnt`` are required; missing any rejects the WHOLE
    enclosing message with DLQ ``reason='invalid_snap_entry'`` (§6).
    """

    model_config = ConfigDict(extra="forbid")

    an: Annotated[int, Field(ge=ANTENNA_MIN, le=ANTENNA_MAX)]
    epc: str
    rssi: Annotated[int, Field(ge=RSSI_MIN, le=RSSI_MAX)]
    cnt: Annotated[int, Field(ge=CNT_MIN, le=CNT_MAX)]
    tmp: Annotated[float | None, Field(ge=TMP_MIN, le=TMP_MAX)] = None
    hum: Annotated[float | None, Field(ge=HUM_MIN, le=HUM_MAX)] = None

    @model_validator(mode="before")
    @classmethod
    def _reject_explicit_null_sensor_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            _reject_explicit_null_optional(data, ("tmp", "hum"))
        return data

    @field_validator("epc")
    @classmethod
    def _normalize_epc(cls, value: str) -> str:
        return _validate_epc(value)


# ---------------------------------------------------------------------------
# Shared envelope mixin — not a Pydantic base, just constants reused below
# ---------------------------------------------------------------------------


class _WmEnvelopeBase(BaseModel):
    """Common envelope fields for all v2 messages (spec §2.2)."""

    model_config = ConfigDict(extra="forbid")

    sn: Annotated[int, Field(ge=0)]
    ts: Annotated[int, Field(ge=0)]


# ---------------------------------------------------------------------------
# t=0 — snapshot
# ---------------------------------------------------------------------------


class WmSnapMessage(_WmEnvelopeBase):
    """Snapshot — complete EPC set currently in field (spec §3.1, §3.3).

    ``epcs`` is required and may be empty (``[]`` means the RF field is
    empty — see spec §3.4). Top-level per-EPC fields (``epc`` / ``an`` /
    ``rssi`` / ``cnt``) are forbidden here — they live inside the
    ``epcs[]`` entries. ``extra="forbid"`` enforces this; an ``epc``
    leaking up to the envelope yields the same DLQ class as any other
    unknown key.
    """

    t: Literal[0]
    lat: float | None = Field(ge=LAT_MIN, le=LAT_MAX)
    lon: float | None = Field(ge=LON_MIN, le=LON_MAX)
    epcs: list[WmSnapEntry]


# ---------------------------------------------------------------------------
# t=1 — appeared (add)
# ---------------------------------------------------------------------------


class WmAppearedMessage(_WmEnvelopeBase):
    """Appeared — one (EPC, antenna) just transitioned absent → present.

    The per-EPC fields are flattened onto the envelope; one MQTT message
    per appearance (spec §3.1). ``epcs`` is forbidden here.
    """

    t: Literal[1]
    lat: float | None = Field(ge=LAT_MIN, le=LAT_MAX)
    lon: float | None = Field(ge=LON_MIN, le=LON_MAX)
    an: Annotated[int, Field(ge=ANTENNA_MIN, le=ANTENNA_MAX)]
    epc: str
    rssi: Annotated[int, Field(ge=RSSI_MIN, le=RSSI_MAX)]
    cnt: Annotated[int, Field(ge=CNT_MIN, le=CNT_MAX)]
    tmp: Annotated[float | None, Field(ge=TMP_MIN, le=TMP_MAX)] = None
    hum: Annotated[float | None, Field(ge=HUM_MIN, le=HUM_MAX)] = None

    @model_validator(mode="before")
    @classmethod
    def _reject_explicit_null_sensor_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            _reject_explicit_null_optional(data, ("tmp", "hum"))
        return data

    @field_validator("epc")
    @classmethod
    def _normalize_epc(cls, value: str) -> str:
        return _validate_epc(value)


# ---------------------------------------------------------------------------
# t=2 — disappeared (sub)
# ---------------------------------------------------------------------------


class WmDisappearedMessage(_WmEnvelopeBase):
    """Disappeared — one EPC just transitioned present → absent.

    Minimal payload: ``t``, ``sn``, ``ts``, ``epc``. ``lat`` / ``lon``
    MAY be present (and nullable) but are not required (spec §2.2:
    "MAY be omitted on ``t=2``"). ``an`` is also optional on t=2 per
    the same row.
    """

    t: Literal[2]
    epc: str
    lat: float | None = Field(default=None, ge=LAT_MIN, le=LAT_MAX)
    lon: float | None = Field(default=None, ge=LON_MIN, le=LON_MAX)
    an: Annotated[int | None, Field(ge=ANTENNA_MIN, le=ANTENNA_MAX)] = None

    @field_validator("epc")
    @classmethod
    def _normalize_epc(cls, value: str) -> str:
        return _validate_epc(value)


# ---------------------------------------------------------------------------
# Discriminated-union root
# ---------------------------------------------------------------------------


WmMessage = Annotated[
    WmSnapMessage | WmAppearedMessage | WmDisappearedMessage,
    Field(discriminator="t"),
]
"""Top-level v2 message — Pydantic dispatches on the integer ``t`` field."""


__all__ = [
    "EPC_MAX_HEX_CHARS",
    "EPC_MIN_HEX_CHARS",
    "SNAP_SOFT_CAP_ENTRIES",
    "WmAppearedMessage",
    "WmDisappearedMessage",
    "WmMessage",
    "WmSnapEntry",
    "WmSnapMessage",
]
