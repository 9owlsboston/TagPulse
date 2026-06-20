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

from dataclasses import dataclass
from datetime import UTC, datetime
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


# ---------------------------------------------------------------------------
# v2.1 — WM compact dialect (Sprint 67, spec §12, ADR-025 Amendment 1)
#
# Opt-in dialect selected by the reserved envelope field ``v == 2``.
# Positional ``epcs[]`` tuples, envelope-level ``ant``, string ``sn``,
# ISO-8601 ``ts``, float ``rssi``, and an ``fw`` firmware field. A single
# uniform 5-tuple ``[epc, rssi, cnt, tmp, hum]`` serves snap (t=0), add
# (t=1), and delete (t=2); on delete the reading slots are null/0 and
# ignored (only ``epc`` is used).
#
# Parsing is hand-rolled (not Pydantic) because the wire element is a
# positional array and the reading slots are deliberately NOT range-
# checked (WM-authoritative — spec §12.3). Failures raise
# :class:`WmV2ParseError` carrying the spec §6 ``reason`` label so the
# subscriber's DLQ path can reuse the existing reason vocabulary.
# ---------------------------------------------------------------------------

WM_V2_VERSION = 2
WM_V2_TUPLE_LEN = (
    5  # minimum epcs[] tuple length [epc, rssi, cnt, tmp, hum]; trailing extras ignored
)


class WmV2ParseError(ValueError):
    """A ``v:2`` compact-dialect parse failure carrying a spec §6 reason."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason


@dataclass(frozen=True)
class WmV2Entry:
    """One decoded ``epcs[]`` tuple. Reading slots are ``None`` on t=2."""

    epc: str
    rssi: float | None
    cnt: int | None
    tmp: float | None
    hum: float | None


@dataclass(frozen=True)
class WmV2Message:
    """A decoded ``v:2`` message. ``ts`` is parsed to a tz-aware UTC datetime.

    ``fw`` is an **opaque** firmware/SW version token — a string (recommended;
    e.g. ``"1.10.2"``) or a number (tolerated for the current WM firmware,
    which emits a float). It is stored verbatim and never compared or parsed,
    so it can evolve to semver without a wire break (spec §12.2).
    """

    t: int
    sn: str
    ts: datetime
    lat: float | None
    lon: float | None
    fw: str | float | None
    ant: int | None
    entries: list[WmV2Entry]


def _v2_num(value: Any) -> float | None:
    """Coerce a wire number to float (``None`` passes through)."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise WmV2ParseError("invalid_snap_entry", f"bool is not a number: {value!r}")
    if isinstance(value, int | float):
        return float(value)
    raise WmV2ParseError("invalid_snap_entry", f"not a number: {value!r}")


def _v2_int(value: Any) -> int | None:
    """Coerce a wire number to int by rounding (``None`` passes through)."""
    n = _v2_num(value)
    return int(round(n)) if n is not None else None


def _v2_fw(value: Any) -> str | float | None:
    """Decode the opaque ``fw`` version token (spec §12.2).

    Accepts a **string** (recommended — semver-friendly, e.g. ``"1.10.2"``)
    or a **number** (the current WM firmware emits a float). Stored verbatim
    and never compared, so ``fw`` can migrate string ↔ number without a wire
    break. ``None`` / omitted passes through. ``bool`` and structured values
    are rejected (not a sensible version token).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise WmV2ParseError("invalid_snap_entry", f"fw must be a string or number: {value!r}")
    if isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return float(value)
    raise WmV2ParseError("invalid_snap_entry", f"fw must be a string or number: {value!r}")


def _v2_sn(value: Any) -> str:
    """Decode the envelope ``sn`` reader id (spec §12.2).

    Accepts a non-empty **string** (recommended — WM emits a provisioning
    UUID) or a **number** (a numeric reader serial, coerced to its string
    form). ``device_id`` is derived from the MQTT topic, so ``sn`` is
    informational; it is stored as a string regardless. ``float`` / ``bool``
    / empty / missing are rejected.
    """
    if isinstance(value, str) and value:
        return value
    if isinstance(value, bool):
        raise WmV2ParseError("missing_required_field", f"sn={value!r}")
    if isinstance(value, int):
        return str(value)
    raise WmV2ParseError("missing_required_field", f"sn={value!r}")


def _v2_coord(value: Any, lo: float, hi: float, name: str) -> float | None:
    """Decode and **range-check** an envelope ``lat`` / ``lon`` (spec §12.2).

    ``None`` / omitted passes through (no GNSS fix). A present value must be
    a number within ``[lo, hi]``; anything else rejects the whole message
    with DLQ ``reason="invalid_location"``.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise WmV2ParseError("invalid_location", f"{name} not a number: {value!r}")
    f = float(value)
    if not (lo <= f <= hi):
        raise WmV2ParseError("invalid_location", f"{name} out of range: {f}")
    return f


def _parse_v2_ts(value: Any) -> datetime:
    """Parse an ISO-8601 ``ts`` string to a tz-aware UTC datetime (spec §12.2)."""
    if not isinstance(value, str) or not value:
        raise WmV2ParseError("invalid_timestamp", repr(value))
    raw = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise WmV2ParseError("invalid_timestamp", value) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _parse_v2_entry(t: int, raw: Any) -> WmV2Entry:
    """Decode one positional ``epcs[]`` tuple (spec §12.3).

    The first five slots are ``[epc, rssi, cnt, tmp, hum]``. Tuples with
    **more** than five elements are accepted — any trailing slots are
    **reserved for future fields** (e.g. a peak-RSSI ``rpk``) and ignored,
    so WM can append fields without a wire break. Fewer than five is a
    genuine malformation and is rejected.
    """
    if not isinstance(raw, list) or len(raw) < WM_V2_TUPLE_LEN:
        raise WmV2ParseError(
            "invalid_snap_entry",
            f"tuple must have at least {WM_V2_TUPLE_LEN} elements: {raw!r}",
        )
    try:
        epc = _validate_epc(raw[0])
    except ValueError as exc:
        raise WmV2ParseError("invalid_epc", str(exc)) from exc
    if t == 2:
        # Delete: reading slots are null/0 placeholders — ignore them.
        return WmV2Entry(epc=epc, rssi=None, cnt=None, tmp=None, hum=None)
    return WmV2Entry(
        epc=epc,
        rssi=_v2_num(raw[1]),
        cnt=_v2_int(raw[2]),
        tmp=_v2_num(raw[3]),
        hum=_v2_num(raw[4]),
    )


def parse_wm_v2(raw: dict[str, Any]) -> WmV2Message:
    """Decode a WM compact-dialect (``v:2``) message (spec §12).

    The caller has already confirmed ``raw`` is a dict carrying a ``v``
    key. Raises :class:`WmV2ParseError` (spec §6 ``reason``) on any
    malformed field. Reading slots are stored as given (never range-
    checked); only ``epc`` and the antenna range are validated.
    """
    v = raw.get("v")
    if v != WM_V2_VERSION:
        raise WmV2ParseError("unknown_wire_version", f"v={v!r}")
    t = raw.get("t")
    if t is None:
        raise WmV2ParseError("missing_type")
    if t not in (0, 1, 2):
        raise WmV2ParseError("unknown_type", f"t={t!r}")
    sn = _v2_sn(raw.get("sn"))
    ts = _parse_v2_ts(raw.get("ts"))
    epcs = raw.get("epcs")
    if not isinstance(epcs, list):
        raise WmV2ParseError("missing_required_field", "epcs must be an array")
    ant = _v2_int(raw.get("ant"))
    if ant is not None and not (ANTENNA_MIN <= ant <= ANTENNA_MAX):
        raise WmV2ParseError("invalid_snap_entry", f"ant out of range: {ant}")
    entries = [_parse_v2_entry(t, e) for e in epcs]
    return WmV2Message(
        t=t,
        sn=sn,
        ts=ts,
        lat=_v2_coord(raw.get("lat"), LAT_MIN, LAT_MAX, "lat"),
        lon=_v2_coord(raw.get("lon"), LON_MIN, LON_MAX, "lon"),
        fw=_v2_fw(raw.get("fw")),
        ant=ant,
        entries=entries,
    )


__all__ = [
    "EPC_MAX_HEX_CHARS",
    "EPC_MIN_HEX_CHARS",
    "SNAP_SOFT_CAP_ENTRIES",
    "WM_V2_TUPLE_LEN",
    "WM_V2_VERSION",
    "WmAppearedMessage",
    "WmDisappearedMessage",
    "WmMessage",
    "WmSnapEntry",
    "WmSnapMessage",
    "WmV2Entry",
    "WmV2Message",
    "WmV2ParseError",
    "parse_wm_v2",
]
