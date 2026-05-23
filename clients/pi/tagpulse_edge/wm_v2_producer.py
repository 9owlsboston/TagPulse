"""Pi-gateway producer for the WM v2 edge wire format (Sprint 47, ADR-025).

Pure-logic producer that turns a sequence of reader **cycle observations**
into v2 MQTT message payloads. The reference implementation of v2's
Shape 2 (Pi-gateway) producer architecture per
:doc:`docs/design/edge-wire-format-v2.md` §1.5.

Design notes
------------

- **Pure logic, no I/O.** ``emit_cycle()`` returns a list of dicts; the
  caller is responsible for JSON-encoding, MQTT publish, QoS 1, retain
  semantics, etc. This makes the producer trivial to unit-test and
  decouples it from any specific MQTT client.
- **Per-(antenna, EPC) keying for the cycle-diff state.** Matches the
  v2 §2.2 per-EPC entry shape: a tag on antenna 1 → antenna 2 emits a
  t=1 for (epc, an=2). ``t=2`` (departure) is **per-EPC**, never
  per-(epc, antenna): an EPC is only "gone" when it leaves every
  antenna (spec §2.2 "one t=2 per departing EPC").
- **Snap triggers (v2 §3.3):** time-based (``snap_period_s``),
  cycle-based (``snap_cycle_count``), and session-based
  (:meth:`begin_session`). Any one triggers a snap on the next
  :meth:`emit_cycle` call.
- **Empty-cycle handling (v2 §3.4):** when a snap is triggered and the
  current cycle is empty, the producer emits a snap with
  ``epcs: []``. When no snap is triggered, an empty current cycle
  whose ``last_cycle`` had entries collapses to ``t=2`` per departing
  EPC; an empty-after-empty cycle emits zero messages (the v2
  "zero on the wire" case).
- **Sensor field omission (v2 §2.2, §6 ``explicit_null``):** when a
  :class:`CycleEpcObservation` has ``tmp=None`` / ``hum=None``, the
  producer **omits** the key from the wire message. It never emits
  ``"tmp": null``. Mirrors the subscriber's ``_reject_explicit_null_*``
  enforcement.
- **EPC validation mirrors the subscriber** (:mod:`tagpulse.ingestion.wm_wire_format`).
  We validate here so the producer fails loudly on bad inputs from the
  reader-to-edge layer instead of generating wire bytes the subscriber
  would silently DLQ. Constants are duplicated locally (not imported
  from the backend) to keep the Pi-gateway package self-contained for
  Pi-side packaging.

See [docs/design/reader-to-edge-contract.md](../../../docs/design/reader-to-edge-contract.md)
for the LAN-side input contract that feeds this producer, and
[ADR-027](../../../docs/adr/027-reader-to-edge-contract.md) for the
LAN-side ratification.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Field constants — mirror src/tagpulse/ingestion/wm_wire_format.py exactly.
# DUPLICATED INTENTIONALLY so the Pi-gateway package has no backend import.
# Any change here MUST be matched in the backend module (and vice versa).
# ---------------------------------------------------------------------------

EPC_MIN_HEX_CHARS = 8
EPC_MAX_HEX_CHARS = 124

ANTENNA_MIN = 0
ANTENNA_MAX = 255

RSSI_MIN = -127
RSSI_MAX = 0

CNT_MIN = 1
CNT_MAX = 65535

LAT_MIN, LAT_MAX = -90.0, 90.0
LON_MIN, LON_MAX = -180.0, 180.0
TMP_MIN, TMP_MAX = -40.0, 85.0
HUM_MIN, HUM_MAX = 0.0, 100.0

SNAP_SOFT_CAP_ENTRIES = 5000


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CycleEpcObservation:
    """One (antenna, EPC) observation aggregated over a single reader cycle.

    Created by the LAN-side reader-to-edge parser per
    :doc:`docs/design/reader-to-edge-contract.md` §3.1. The producer
    treats one ``CycleEpcObservation`` per ``(antenna, epc)`` per cycle
    as the unit of cycle state. Duplicate ``(antenna, epc)`` within one
    cycle is a LAN-side bug and the parser is responsible for
    deduplicating before handing the cycle to this producer.

    Fields mirror v2 §2.2 per-EPC entry semantics:

    - ``an`` 0..255 (0 = unknown/muxed antenna)
    - ``epc`` uppercase hex, 8..124 chars, even length
    - ``rssi`` -127..0 dBm
    - ``cnt`` 1..65535 reads of this (epc, an) in this cycle
    - ``tmp`` -40..85 °C, ``None`` if sensor read failed or absent
    - ``hum`` 0..100 %RH, ``None`` if sensor read failed or absent
    """

    an: int
    epc: str
    rssi: int
    cnt: int
    tmp: float | None = None
    hum: float | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_epc(value: str) -> str:
    """Validate + canonicalize an EPC. Mirrors backend ``_validate_epc``."""
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


def _validate_obs(obs: CycleEpcObservation) -> CycleEpcObservation:
    """Validate one ``CycleEpcObservation`` and return its canonicalized form."""
    if not (ANTENNA_MIN <= obs.an <= ANTENNA_MAX):
        raise ValueError(f"an {obs.an} outside {ANTENNA_MIN}..{ANTENNA_MAX}")
    epc = _validate_epc(obs.epc)
    if not (RSSI_MIN <= obs.rssi <= RSSI_MAX):
        raise ValueError(f"rssi {obs.rssi} outside {RSSI_MIN}..{RSSI_MAX}")
    if not (CNT_MIN <= obs.cnt <= CNT_MAX):
        raise ValueError(f"cnt {obs.cnt} outside {CNT_MIN}..{CNT_MAX}")
    if obs.tmp is not None and not (TMP_MIN <= obs.tmp <= TMP_MAX):
        raise ValueError(f"tmp {obs.tmp} outside {TMP_MIN}..{TMP_MAX}")
    if obs.hum is not None and not (HUM_MIN <= obs.hum <= HUM_MAX):
        raise ValueError(f"hum {obs.hum} outside {HUM_MIN}..{HUM_MAX}")
    if obs.epc == epc:
        return obs
    return CycleEpcObservation(
        an=obs.an, epc=epc, rssi=obs.rssi, cnt=obs.cnt, tmp=obs.tmp, hum=obs.hum
    )


def _validate_lat_lon(lat: float | None, lon: float | None) -> None:
    if lat is not None and not (LAT_MIN <= lat <= LAT_MAX):
        raise ValueError(f"lat {lat} outside {LAT_MIN}..{LAT_MAX}")
    if lon is not None and not (LON_MIN <= lon <= LON_MAX):
        raise ValueError(f"lon {lon} outside {LON_MIN}..{LON_MAX}")


def _entry_dict(obs: CycleEpcObservation) -> dict[str, Any]:
    """Build a t=0 ``epcs[]`` entry dict. Omits sensor keys when ``None``."""
    entry: dict[str, Any] = {
        "an": obs.an,
        "epc": obs.epc,
        "rssi": obs.rssi,
        "cnt": obs.cnt,
    }
    if obs.tmp is not None:
        entry["tmp"] = obs.tmp
    if obs.hum is not None:
        entry["hum"] = obs.hum
    return entry


# ---------------------------------------------------------------------------
# Producer
# ---------------------------------------------------------------------------


@dataclass
class WmV2Producer:
    """Cycle-diff producer for v2 wire messages.

    Construct once per (device, MQTT session) and feed each LAN-side
    cycle into :meth:`emit_cycle`. Call :meth:`begin_session` after
    MQTT connect/reconnect and after a reader reset (per ADR-027) so
    the next cycle is force-promoted to a snap (v2 §3.3 trigger 3).

    Profile selection (spec §3.8):

    - **Profile A — delta (default).** ``snap_period_s=300.0``,
      ``snap_cycle_count=100``. The common case: emits t=0 only at
      session start, every 300 s, or every 100 cycles; emits t=1/t=2
      between snaps.
    - **Profile B — snap-only.** Set ``snap_cycle_count=1`` (and
      ``snap_period_s=0.0`` to disable the time gate as redundant).
      Every cycle emits a single t=0 snap with the current set; t=1 /
      t=2 are never produced. Cheaper for very small reader SKUs whose
      cycle-diff state would dominate their RAM.
    - **Profile C — legacy / pass-through.** Out of scope here; spec
      §3.8 covers it for v1-only producers that never adopt v2.

    The producer never opens a socket, never publishes, never blocks.
    All time is supplied by the caller via the ``ts_ms`` argument to
    :meth:`emit_cycle`.
    """

    sn: int
    """Wire envelope ``sn`` (device serial, v2 §2.2)."""

    snap_period_s: float = 300.0
    """Maximum **seconds between snaps** (v2 §3.3 trigger 1).

    - ``> 0`` (default 300): once this many seconds pass since the last
      snap, the next ``emit_cycle`` is promoted to a snap.
    - ``0``: time-based trigger fires on every cycle (degenerate).
    - ``< 0``: time-based trigger disabled.
    """

    snap_cycle_count: int = 100
    """Maximum **delta cycles between snaps** (v2 §3.3 trigger 2).

    - ``> 0`` (default 100): after this many delta cycles since the
      last snap, the next ``emit_cycle`` is promoted to a snap.
    - ``0``: every cycle is a snap (Profile B — spec §3.8).
    - ``< 0``: cycle-based trigger disabled.
    """

    _last_cycle: dict[tuple[int, str], CycleEpcObservation] = field(
        default_factory=dict, init=False, repr=False
    )
    _last_snap_ts_ms: int | None = field(default=None, init=False, repr=False)
    _cycles_since_snap: int = field(default=0, init=False, repr=False)
    _force_snap_next: bool = field(default=True, init=False, repr=False)
    """Initial state: first ever ``emit_cycle`` is a snap (session trigger)."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def begin_session(self) -> None:
        """Mark next ``emit_cycle`` as the start of a new MQTT session.

        Called after MQTT connect/reconnect (v2 §3.3 trigger 3) and after
        a reader reset (ADR-027 §5: explicit ``reset`` record or
        ``reader_ts_ms`` regression). Clears the cycle-diff state and
        forces the next emit to a snap.

        Note: clearing ``_last_cycle`` is critical — after a reconnect
        the producer has no way to know what the subscriber's
        ``tag_presence`` state is, so it cannot legitimately compute
        deltas. The forced snap re-syncs both sides.
        """
        self._last_cycle.clear()
        self._cycles_since_snap = 0
        self._force_snap_next = True

    def emit_cycle(
        self,
        ts_ms: int,
        lat: float | None,
        lon: float | None,
        cycle: Iterable[CycleEpcObservation],
    ) -> list[dict[str, Any]]:
        """Process one reader cycle, return v2 wire message dicts.

        :param ts_ms: cycle wall-clock timestamp in epoch milliseconds (UTC).
            Per ADR-027 §3.2, the Pi-gateway owns the wall clock — pass
            ``int(time.time() * 1000)`` measured at cycle-end.
        :param lat: location latitude (-90..90) or ``None`` if unknown.
            Forwarded to t=0 / t=1 wire envelopes (v2 §2.2; lat/lon are
            required-but-nullable on those types). t=2 omits lat/lon
            entirely for bandwidth.
        :param lon: location longitude (-180..180) or ``None``.
        :param cycle: iterable of :class:`CycleEpcObservation`. May be
            empty (empty inventory cycle). Caller MUST deduplicate by
            ``(antenna, epc)`` before passing — duplicate keys raise.
        :returns: list of dicts. Each dict is one MQTT message
            payload ready for ``json.dumps()`` + QoS-1 publish on the
            device's tag-reads topic. Order is the publish order.
        """
        if ts_ms < 0:
            raise ValueError(f"ts_ms must be non-negative, got {ts_ms}")
        _validate_lat_lon(lat, lon)

        # Materialize + validate the cycle, keyed by (an, epc). Detect
        # duplicate keys as a hard error — the LAN-side parser is
        # responsible for collapsing those before we see them.
        current: dict[tuple[int, str], CycleEpcObservation] = {}
        for obs in cycle:
            checked = _validate_obs(obs)
            key = (checked.an, checked.epc)
            if key in current:
                raise ValueError(
                    f"duplicate (an, epc) {key!r} in cycle; "
                    "LAN-side parser must dedupe before emit_cycle"
                )
            current[key] = checked

        # Decide if this cycle is a snap.
        snap_trigger = self._snap_triggered(ts_ms)
        if snap_trigger:
            return self._emit_snap(ts_ms, lat, lon, current)
        return self._emit_delta(ts_ms, lat, lon, current)

    # ------------------------------------------------------------------
    # Snap decision
    # ------------------------------------------------------------------

    def _snap_triggered(self, ts_ms: int) -> bool:
        """Apply v2 §3.3 snap-trigger rules."""
        if self._force_snap_next:
            return True
        if self._last_snap_ts_ms is None:
            # Should not happen — _force_snap_next is True at construction
            # so the first cycle always snaps and sets _last_snap_ts_ms.
            return True
        if self.snap_period_s >= 0 and (ts_ms - self._last_snap_ts_ms) >= self.snap_period_s * 1000:
            return True
        return self.snap_cycle_count >= 0 and self._cycles_since_snap >= self.snap_cycle_count

    # ------------------------------------------------------------------
    # Snap emit (t=0)
    # ------------------------------------------------------------------

    def _emit_snap(
        self,
        ts_ms: int,
        lat: float | None,
        lon: float | None,
        current: dict[tuple[int, str], CycleEpcObservation],
    ) -> list[dict[str, Any]]:
        entries = [_entry_dict(obs) for obs in current.values()]
        if len(entries) > SNAP_SOFT_CAP_ENTRIES:
            logger.warning(
                "wm_v2_producer.snap_soft_cap_exceeded",
                extra={"snap_entries": len(entries), "soft_cap": SNAP_SOFT_CAP_ENTRIES},
            )
        msg: dict[str, Any] = {
            "t": 0,
            "sn": self.sn,
            "ts": ts_ms,
            "lat": lat,
            "lon": lon,
            "epcs": entries,
        }
        # Update state AFTER successful build.
        self._last_cycle = dict(current)
        self._last_snap_ts_ms = ts_ms
        self._cycles_since_snap = 0
        self._force_snap_next = False
        return [msg]

    # ------------------------------------------------------------------
    # Delta emit (t=1 + t=2)
    # ------------------------------------------------------------------

    def _emit_delta(
        self,
        ts_ms: int,
        lat: float | None,
        lon: float | None,
        current: dict[tuple[int, str], CycleEpcObservation],
    ) -> list[dict[str, Any]]:
        # Adds: (an, epc) keys present in current but not in last.
        add_keys = sorted(current.keys() - self._last_cycle.keys())
        # Subs: EPCs that were in last_cycle but are not in current_cycle
        # (under ANY antenna). One t=2 per departing EPC.
        last_epcs = {epc for (_an, epc) in self._last_cycle}
        current_epcs = {epc for (_an, epc) in current}
        sub_epcs = sorted(last_epcs - current_epcs)

        msgs: list[dict[str, Any]] = []
        for key in add_keys:
            obs = current[key]
            appeared: dict[str, Any] = {
                "t": 1,
                "sn": self.sn,
                "ts": ts_ms,
                "lat": lat,
                "lon": lon,
                "an": obs.an,
                "epc": obs.epc,
                "rssi": obs.rssi,
                "cnt": obs.cnt,
            }
            if obs.tmp is not None:
                appeared["tmp"] = obs.tmp
            if obs.hum is not None:
                appeared["hum"] = obs.hum
            msgs.append(appeared)

        for epc in sub_epcs:
            # Per spec §2.2: t=2 carries epc; lat/lon/an MAY be omitted.
            # We omit for minimal bandwidth — matches the subscriber's
            # WmDisappearedMessage defaults.
            msgs.append({"t": 2, "sn": self.sn, "ts": ts_ms, "epc": epc})

        # Update state AFTER successful build.
        self._last_cycle = dict(current)
        self._cycles_since_snap += 1
        return msgs


__all__ = [
    "ANTENNA_MAX",
    "ANTENNA_MIN",
    "CNT_MAX",
    "CNT_MIN",
    "EPC_MAX_HEX_CHARS",
    "EPC_MIN_HEX_CHARS",
    "HUM_MAX",
    "HUM_MIN",
    "LAT_MAX",
    "LAT_MIN",
    "LON_MAX",
    "LON_MIN",
    "RSSI_MAX",
    "RSSI_MIN",
    "SNAP_SOFT_CAP_ENTRIES",
    "TMP_MAX",
    "TMP_MIN",
    "CycleEpcObservation",
    "WmV2Producer",
]
