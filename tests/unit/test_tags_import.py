"""Unit tests for the Sprint 50 C1 tag-import CSV pipeline.

Covers the pure-Python pieces (CSV parser + per-hour rate limiter) +
the schema shapes. Route-level integration coverage layers on at the
DB-backed integration suite in a later sprint.
"""

from __future__ import annotations

import uuid

from tagpulse.core.tag_import_rate_limit import TAG_IMPORT_LIMITER, _HourlyLimiter
from tagpulse.models.schemas import TagImportResult, TagImportRowError
from tagpulse.services.tags import TagImportRow, parse_tag_import_csv

# Valid 24-hex EPC samples used across tests.
_EPC_A = "3034257BF400B7800004CB2F"
_EPC_B = "3034257BF400B7800004CB30"
_EPC_C = "3034257BF400B7800004CB31"


def _csv(*lines: str) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# parse_tag_import_csv — happy path
# ---------------------------------------------------------------------------


class TestParseTagImportCsvHappyPath:
    def test_returns_rows_when_all_valid(self) -> None:
        raw = _csv("epc_hex", _EPC_A, _EPC_B)
        rows, errors = parse_tag_import_csv(raw)
        assert errors == []
        assert rows == [TagImportRow(epc_hex=_EPC_A), TagImportRow(epc_hex=_EPC_B)]

    def test_canonicalises_input(self) -> None:
        raw = _csv("epc_hex", _EPC_A.lower(), f"  {_EPC_B}  ")
        rows, errors = parse_tag_import_csv(raw)
        assert errors == []
        assert [r.epc_hex for r in rows] == [_EPC_A, _EPC_B]

    def test_header_match_is_case_insensitive(self) -> None:
        raw = _csv("EPC_HEX", _EPC_A)
        rows, errors = parse_tag_import_csv(raw)
        assert errors == []
        assert rows == [TagImportRow(epc_hex=_EPC_A)]

    def test_extra_columns_are_ignored(self) -> None:
        raw = _csv("epc_hex,batch,note", f"{_EPC_A},B-001,ignored")
        rows, errors = parse_tag_import_csv(raw)
        assert errors == []
        assert rows == [TagImportRow(epc_hex=_EPC_A)]

    def test_accepts_utf8_bom(self) -> None:
        raw = b"\xef\xbb\xbf" + _csv("epc_hex", _EPC_A)
        rows, errors = parse_tag_import_csv(raw)
        assert errors == []
        assert rows == [TagImportRow(epc_hex=_EPC_A)]


# ---------------------------------------------------------------------------
# parse_tag_import_csv — error surface
# ---------------------------------------------------------------------------


class TestParseTagImportCsvErrors:
    def test_empty_file_reports_row_zero(self) -> None:
        rows, errors = parse_tag_import_csv(b"")
        assert rows == []
        assert len(errors) == 1
        assert errors[0].row == 0

    def test_missing_required_column(self) -> None:
        raw = _csv("foo,bar", "1,2")
        rows, errors = parse_tag_import_csv(raw)
        assert rows == []
        assert len(errors) == 1
        assert "epc_hex" in errors[0].error

    def test_invalid_utf8_reports_row_zero(self) -> None:
        # 0xFF is not valid UTF-8 in any leading-byte position.
        rows, errors = parse_tag_import_csv(b"\xff\xfe\xfd")
        assert rows == []
        assert len(errors) == 1
        assert errors[0].row == 0

    def test_blank_epc_row_reported(self) -> None:
        # csv.DictReader skips truly empty lines; a row with an empty
        # cell (``,,``) is the realistic "operator forgot to fill a cell"
        # case and must surface as a per-line error.
        raw = _csv("epc_hex,note", f"{_EPC_A},ok", ",missing-epc", f"{_EPC_B},ok")
        rows, errors = parse_tag_import_csv(raw)
        assert [r.epc_hex for r in rows] == [_EPC_A, _EPC_B]
        assert [(e.row, e.error) for e in errors] == [(2, "epc_hex is required")]

    def test_invalid_hex_reported_with_original_value(self) -> None:
        raw = _csv("epc_hex", "not-a-hex-string")
        rows, errors = parse_tag_import_csv(raw)
        assert rows == []
        assert len(errors) == 1
        assert errors[0].row == 1
        assert errors[0].epc_hex == "not-a-hex-string"

    def test_too_short_epc_reported(self) -> None:
        raw = _csv("epc_hex", "DEADBEEF")  # 8 hex chars, min is 16
        rows, errors = parse_tag_import_csv(raw)
        assert rows == []
        assert len(errors) == 1

    def test_in_csv_duplicate_reported(self) -> None:
        raw = _csv("epc_hex", _EPC_A, _EPC_B, _EPC_A)
        rows, errors = parse_tag_import_csv(raw)
        assert [r.epc_hex for r in rows] == [_EPC_A, _EPC_B]
        assert len(errors) == 1
        assert errors[0].row == 3
        assert "duplicate" in errors[0].error
        # Cross-reference back to the first-seen row.
        assert "row 1" in errors[0].error

    def test_in_csv_duplicate_treats_case_as_equivalent(self) -> None:
        raw = _csv("epc_hex", _EPC_A, _EPC_A.lower())
        rows, errors = parse_tag_import_csv(raw)
        assert len(rows) == 1
        assert len(errors) == 1


# ---------------------------------------------------------------------------
# Hourly per-tenant rate limiter
# ---------------------------------------------------------------------------


class TestHourlyLimiter:
    def test_allows_up_to_cap(self) -> None:
        limiter = _HourlyLimiter()
        tenant = uuid.uuid4()
        assert all(limiter.check_and_record(tenant, 3) for _ in range(3))
        assert limiter.check_and_record(tenant, 3) is False

    def test_zero_cap_blocks_everything(self) -> None:
        limiter = _HourlyLimiter()
        assert limiter.check_and_record(uuid.uuid4(), 0) is False

    def test_window_slides(self) -> None:
        limiter = _HourlyLimiter()
        tenant = uuid.uuid4()
        # Inject monotonic-style clock readings.
        for _ in range(2):
            assert limiter.check_and_record(tenant, 2, now=100.0) is True
        # Same second: capped.
        assert limiter.check_and_record(tenant, 2, now=100.0) is False
        # 1 hour + 1 second later: window has rolled, both events expire.
        assert limiter.check_and_record(tenant, 2, now=100.0 + 3601.0) is True

    def test_per_tenant_isolation(self) -> None:
        limiter = _HourlyLimiter()
        a, b = uuid.uuid4(), uuid.uuid4()
        for _ in range(3):
            assert limiter.check_and_record(a, 3) is True
        assert limiter.check_and_record(a, 3) is False
        # B has its own bucket.
        assert limiter.check_and_record(b, 3) is True

    def test_remaining_counts_correctly(self) -> None:
        limiter = _HourlyLimiter()
        tenant = uuid.uuid4()
        assert limiter.remaining(tenant, 5) == 5
        limiter.check_and_record(tenant, 5)
        limiter.check_and_record(tenant, 5)
        assert limiter.remaining(tenant, 5) == 3

    def test_reset_clears_state(self) -> None:
        limiter = _HourlyLimiter()
        tenant = uuid.uuid4()
        for _ in range(3):
            limiter.check_and_record(tenant, 3)
        assert limiter.check_and_record(tenant, 3) is False
        limiter.reset()
        assert limiter.check_and_record(tenant, 3) is True

    def test_module_singleton_exists(self) -> None:
        # Sanity: the route layer imports this exact object.
        assert isinstance(TAG_IMPORT_LIMITER, _HourlyLimiter)


# ---------------------------------------------------------------------------
# TagImportResult schema
# ---------------------------------------------------------------------------


class TestTagImportResult:
    def test_empty_errors_default(self) -> None:
        result = TagImportResult(rows_total=5, rows_created=5, rows_skipped=0, dry_run=False)
        assert result.errors == []

    def test_serialises_errors(self) -> None:
        err = TagImportRowError(row=3, epc_hex="bad", error="invalid")
        result = TagImportResult(
            rows_total=1, rows_created=0, rows_skipped=0, dry_run=True, errors=[err]
        )
        dumped = result.model_dump()
        assert dumped["dry_run"] is True
        assert dumped["errors"] == [{"row": 3, "epc_hex": "bad", "error": "invalid"}]
