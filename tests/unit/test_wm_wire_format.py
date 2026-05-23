"""Unit tests for the WM v2 wire-format Pydantic models (Sprint 46, ADR-025).

Covers the spec §6 rejection table and the §3 happy paths. Identity
resolution (``sn`` → ``device_id``), JWT cross-checks, clock-skew
gating, and reconciliation are out of scope for these tests — they live
in the subscriber + reconciler integration tests (Phase C / D).
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from tagpulse.ingestion.wm_wire_format import (
    WmAppearedMessage,
    WmDisappearedMessage,
    WmMessage,
    WmSnapMessage,
)

_ADAPTER: TypeAdapter[Any] = TypeAdapter(WmMessage)


def _parse(payload: dict[str, Any]) -> Any:
    return _ADAPTER.validate_python(payload)


# ---------------------------------------------------------------------------
# Happy paths (spec §3 examples)
# ---------------------------------------------------------------------------


class TestSnap:
    def test_full_snap_parses(self) -> None:
        msg = _parse(
            {
                "t": 0,
                "sn": 123,
                "ts": 1716489732001,
                "lat": 41.40338,
                "lon": 2.17403,
                "epcs": [
                    {
                        "an": 1,
                        "epc": "e2801160aaaa",
                        "rssi": -48,
                        "cnt": 2,
                        "tmp": 23.45,
                        "hum": 41.2,
                    },
                    {"an": 1, "epc": "E2801160BBBB", "rssi": -52, "cnt": 1},
                ],
            }
        )
        assert isinstance(msg, WmSnapMessage)
        assert msg.t == 0
        # EPC normalised to upper case (spec §2.2).
        assert msg.epcs[0].epc == "E2801160AAAA"
        assert msg.epcs[0].tmp == 23.45
        assert msg.epcs[1].tmp is None

    def test_empty_snap_parses(self) -> None:
        # Spec §3.4 — empty RF field is signalled by an empty array.
        msg = _parse(
            {
                "t": 0,
                "sn": 123,
                "ts": 1716489732001,
                "lat": 41.40338,
                "lon": 2.17403,
                "epcs": [],
            }
        )
        assert isinstance(msg, WmSnapMessage)
        assert msg.epcs == []

    def test_no_gnss_fix_snap_parses(self) -> None:
        # Spec §2.2 — lat/lon are nullable on t=0.
        msg = _parse(
            {
                "t": 0,
                "sn": 123,
                "ts": 1716489732001,
                "lat": None,
                "lon": None,
                "epcs": [],
            }
        )
        assert msg.lat is None
        assert msg.lon is None


class TestAppeared:
    def test_t1_parses(self) -> None:
        msg = _parse(
            {
                "t": 1,
                "sn": 123,
                "ts": 1716489732001,
                "lat": 41.40338,
                "lon": 2.17403,
                "an": 1,
                "epc": "E2801160CCCC",
                "rssi": -50,
                "cnt": 1,
                "tmp": 23.5,
                "hum": 41.0,
            }
        )
        assert isinstance(msg, WmAppearedMessage)
        assert msg.epc == "E2801160CCCC"

    def test_t1_without_optional_sensors(self) -> None:
        msg = _parse(
            {
                "t": 1,
                "sn": 1,
                "ts": 1,
                "lat": None,
                "lon": None,
                "an": 1,
                "epc": "E2801160CCCC",
                "rssi": -50,
                "cnt": 1,
            }
        )
        assert msg.tmp is None
        assert msg.hum is None


class TestDisappeared:
    def test_minimal_t2_parses(self) -> None:
        msg = _parse({"t": 2, "sn": 123, "ts": 1716489732001, "epc": "E2801160FFFF"})
        assert isinstance(msg, WmDisappearedMessage)
        assert msg.epc == "E2801160FFFF"
        assert msg.lat is None
        assert msg.lon is None
        assert msg.an is None


# ---------------------------------------------------------------------------
# Rejection paths (spec §6 table)
# ---------------------------------------------------------------------------


class TestRejections:
    def test_missing_t_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _parse({"sn": 1, "ts": 1, "epc": "E2801160AAAA"})

    def test_unknown_t_value_rejected(self) -> None:
        # Spec §6 unknown_type — only 0/1/2 are valid in v2.0.
        with pytest.raises(ValidationError):
            _parse({"t": 9, "sn": 1, "ts": 1})

    def test_epcs_on_t1_rejected(self) -> None:
        # Spec §6 epcs_wrong_type — forbidden on deltas.
        with pytest.raises(ValidationError):
            _parse(
                {
                    "t": 1,
                    "sn": 1,
                    "ts": 1,
                    "lat": None,
                    "lon": None,
                    "an": 1,
                    "epc": "E2801160AAAA",
                    "rssi": -40,
                    "cnt": 1,
                    "epcs": [],
                }
            )

    def test_epcs_on_t2_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _parse(
                {
                    "t": 2,
                    "sn": 1,
                    "ts": 1,
                    "epc": "E2801160AAAA",
                    "epcs": [],
                }
            )

    def test_epcs_missing_on_t0_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _parse({"t": 0, "sn": 1, "ts": 1, "lat": None, "lon": None})

    def test_lat_missing_on_t0_rejected(self) -> None:
        # Spec §6 missing_required_field — lat/lon required keys on t=0.
        with pytest.raises(ValidationError):
            _parse({"t": 0, "sn": 1, "ts": 1, "lon": None, "epcs": []})

    def test_lon_missing_on_t1_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _parse(
                {
                    "t": 1,
                    "sn": 1,
                    "ts": 1,
                    "lat": None,
                    "an": 1,
                    "epc": "E2801160AAAA",
                    "rssi": -40,
                    "cnt": 1,
                }
            )

    def test_snap_entry_missing_field_rejected(self) -> None:
        # Spec §6 invalid_snap_entry — entry missing rssi rejects whole message.
        with pytest.raises(ValidationError):
            _parse(
                {
                    "t": 0,
                    "sn": 1,
                    "ts": 1,
                    "lat": None,
                    "lon": None,
                    "epcs": [{"an": 1, "epc": "E2801160AAAA", "cnt": 1}],
                }
            )

    def test_explicit_null_tmp_rejected(self) -> None:
        # Spec §6 explicit_null — optional sensor fields must be omitted.
        with pytest.raises(ValidationError):
            _parse(
                {
                    "t": 1,
                    "sn": 1,
                    "ts": 1,
                    "lat": None,
                    "lon": None,
                    "an": 1,
                    "epc": "E2801160AAAA",
                    "rssi": -40,
                    "cnt": 1,
                    "tmp": None,
                }
            )

    def test_explicit_null_hum_in_snap_entry_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _parse(
                {
                    "t": 0,
                    "sn": 1,
                    "ts": 1,
                    "lat": None,
                    "lon": None,
                    "epcs": [
                        {
                            "an": 1,
                            "epc": "E2801160AAAA",
                            "rssi": -40,
                            "cnt": 1,
                            "hum": None,
                        }
                    ],
                }
            )

    @pytest.mark.parametrize(
        "bad_epc",
        [
            "ABC",  # odd length
            "ABCDE",  # odd length
            "ZZZZZZZZ",  # non-hex
            "AB",  # below floor (8 chars)
            "A" * 126,  # above ceiling (124 chars)
        ],
    )
    def test_invalid_epc_rejected(self, bad_epc: str) -> None:
        # Spec §6 invalid_epc — odd length, non-hex, or out of range.
        with pytest.raises(ValidationError):
            _parse({"t": 2, "sn": 1, "ts": 1, "epc": bad_epc})

    def test_rssi_above_range_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _parse(
                {
                    "t": 1,
                    "sn": 1,
                    "ts": 1,
                    "lat": None,
                    "lon": None,
                    "an": 1,
                    "epc": "E2801160AAAA",
                    "rssi": 5,
                    "cnt": 1,
                }
            )

    def test_lat_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _parse(
                {
                    "t": 0,
                    "sn": 1,
                    "ts": 1,
                    "lat": 91.0,
                    "lon": 0.0,
                    "epcs": [],
                }
            )

    def test_reserved_field_rejected(self) -> None:
        # Spec §2.2 — reserved keys (``v``, ``hb``, ``err``, ``cfg``, ``seq``)
        # MUST NOT appear in v2.0. ``extra="forbid"`` enforces this.
        with pytest.raises(ValidationError):
            _parse(
                {
                    "t": 2,
                    "sn": 1,
                    "ts": 1,
                    "epc": "E2801160AAAA",
                    "seq": 5,
                }
            )
