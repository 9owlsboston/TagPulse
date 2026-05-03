"""RFID EPC decoder utilities (Sprint 14).

Pure-Python decoder for the common GS1 EPC encoding schemes:
SGTIN-96, SGTIN-198, SSCC-96, GIAI-96, GIAI-202, GRAI-96, GRAI-170.

Only the bit-layout per [GS1 EPC TDS] is needed to recover the URI form
and structured parts. Unknown schemes fall back to ``("raw", {})``.

References:
    - GS1 EPC Tag Data Standard 1.13
    - docs/design/rfid-tag-data-model.md §5

The decoder is best-effort — a malformed or unknown header byte returns
``("raw", {})`` with no exception so the ingestion pipeline can persist
``epc_hex`` and continue.
"""

from __future__ import annotations

from typing import Any

# Header byte → (scheme, total_bits)
# Per GS1 EPC TDS Table 14-1 (subset of common schemes).
_HEADERS: dict[int, tuple[str, int]] = {
    0x30: ("sgtin-96", 96),
    0x36: ("sgtin-198", 198),
    0x31: ("sscc-96", 96),
    0x34: ("giai-96", 96),
    0x38: ("giai-202", 202),
    0x33: ("grai-96", 96),
    0x37: ("grai-170", 170),
}

# Partition table for SGTIN/SSCC/GIAI/GRAI: company-prefix and item bit widths
# indexed by the 3-bit ``partition`` value. Per GS1 EPC TDS Table 14-2/3.
# (cp_bits, item_bits, cp_digits, item_digits)
_SGTIN_PARTITIONS = [
    (40, 4, 12, 1),
    (37, 7, 11, 2),
    (34, 10, 10, 3),
    (30, 14, 9, 4),
    (27, 17, 8, 5),
    (24, 20, 7, 6),
    (20, 24, 6, 7),
]

_SSCC_PARTITIONS = [
    (40, 18, 12, 5),
    (37, 21, 11, 6),
    (34, 24, 10, 7),
    (30, 28, 9, 8),
    (27, 31, 8, 9),
    (24, 34, 7, 10),
    (20, 38, 6, 11),
]

_GIAI_96_PARTITIONS = [
    (40, 42, 12, 13),
    (37, 45, 11, 14),
    (34, 48, 10, 15),
    (30, 52, 9, 17),
    (27, 55, 8, 18),
    (24, 58, 7, 19),
    (20, 62, 6, 20),
]

# GIAI-202: variable-length serial encoded as 7-bit ASCII chars (max 18 chars).
_GIAI_202_PARTITIONS = [
    (40, 148, 12, 18),
    (37, 151, 11, 18),
    (34, 154, 10, 18),
    (30, 158, 9, 18),
    (27, 161, 8, 18),
    (24, 164, 7, 18),
    (20, 168, 6, 18),
]

_GRAI_96_PARTITIONS = [
    (40, 4 + 38, 12, 0),  # 4-bit reserved + 38-bit serial; cp+asset_type after
    (37, 4 + 41, 11, 0),
    (34, 4 + 44, 10, 0),
    (30, 4 + 48, 9, 0),
    (27, 4 + 51, 8, 0),
    (24, 4 + 54, 7, 0),
    (20, 4 + 58, 6, 0),
]


def decode_epc_hex(hex_str: str) -> tuple[str, dict[str, Any]]:
    """Decode an EPC wire-format hex string.

    Returns ``(scheme, decoded)`` where ``decoded`` includes ``uri`` plus
    scheme-specific parts. Unknown schemes return ``("raw", {})``.
    """
    if not hex_str:
        return ("raw", {})
    cleaned = hex_str.strip().replace(" ", "").lower()
    try:
        raw = bytes.fromhex(cleaned)
    except ValueError:
        return ("raw", {})
    if len(raw) < 1:
        return ("raw", {})
    header = raw[0]
    info = _HEADERS.get(header)
    if info is None:
        return ("raw", {})
    scheme, total_bits = info
    bits = _BitReader(raw, total_bits)
    bits.read(8)  # consume header
    try:
        if scheme == "sgtin-96":
            return scheme, _decode_sgtin_96(bits)
        if scheme == "sgtin-198":
            return scheme, _decode_sgtin_198(bits)
        if scheme == "sscc-96":
            return scheme, _decode_sscc_96(bits)
        if scheme == "giai-96":
            return scheme, _decode_giai_96(bits)
        if scheme == "giai-202":
            return scheme, _decode_giai_202(bits)
        if scheme == "grai-96":
            return scheme, _decode_grai_96(bits)
        if scheme == "grai-170":
            return scheme, _decode_grai_170(bits)
    except (ValueError, IndexError):
        return ("raw", {})
    return ("raw", {})


class _BitReader:
    """Sequential bit reader over a bytes buffer."""

    def __init__(self, data: bytes, max_bits: int) -> None:
        self._data = data
        self._pos = 0
        self._max = min(max_bits, len(data) * 8)

    def read(self, n: int) -> int:
        if self._pos + n > self._max:
            raise IndexError("EPC truncated")
        value = 0
        for _ in range(n):
            byte = self._data[self._pos // 8]
            bit = (byte >> (7 - (self._pos % 8))) & 0x1
            value = (value << 1) | bit
            self._pos += 1
        return value


def _read_partition(
    bits: _BitReader,
    table: list[tuple[int, int, int, int]],
) -> tuple[int, int, int, int, int]:
    """Read filter+partition; return (filter, cp_int, item_int, cp_digits, item_digits)."""
    filter_bits = bits.read(3)
    partition = bits.read(3)
    cp_bits, item_bits, cp_digits, item_digits = table[partition]
    cp = bits.read(cp_bits)
    item = bits.read(item_bits)
    return filter_bits, cp, item, cp_digits, item_digits


def _decode_sgtin_96(bits: _BitReader) -> dict[str, Any]:
    filter_bits, cp, indicator_item, cp_digits, item_digits = _read_partition(
        bits, _SGTIN_PARTITIONS
    )
    serial = bits.read(38)
    company_prefix = str(cp).zfill(cp_digits)
    item_ref = str(indicator_item).zfill(item_digits)
    return {
        "scheme": "sgtin-96",
        "filter": filter_bits,
        "company_prefix": company_prefix,
        "item_ref": item_ref,
        "serial": str(serial),
        "uri": f"urn:epc:id:sgtin:{company_prefix}.{item_ref}.{serial}",
    }


def _decode_sgtin_198(bits: _BitReader) -> dict[str, Any]:
    filter_bits, cp, indicator_item, cp_digits, item_digits = _read_partition(
        bits, _SGTIN_PARTITIONS
    )
    serial = _read_7bit_string(bits, max_chars=20)
    company_prefix = str(cp).zfill(cp_digits)
    item_ref = str(indicator_item).zfill(item_digits)
    return {
        "scheme": "sgtin-198",
        "filter": filter_bits,
        "company_prefix": company_prefix,
        "item_ref": item_ref,
        "serial": serial,
        "uri": f"urn:epc:id:sgtin:{company_prefix}.{item_ref}.{serial}",
    }


def _decode_sscc_96(bits: _BitReader) -> dict[str, Any]:
    filter_bits, cp, serial_ref, cp_digits, sr_digits = _read_partition(
        bits, _SSCC_PARTITIONS
    )
    bits.read(24)  # reserved
    company_prefix = str(cp).zfill(cp_digits)
    serial = str(serial_ref).zfill(sr_digits)
    return {
        "scheme": "sscc-96",
        "filter": filter_bits,
        "company_prefix": company_prefix,
        "serial_reference": serial,
        "uri": f"urn:epc:id:sscc:{company_prefix}.{serial}",
    }


def _decode_giai_96(bits: _BitReader) -> dict[str, Any]:
    filter_bits, cp, asset, cp_digits, _ = _read_partition(bits, _GIAI_96_PARTITIONS)
    company_prefix = str(cp).zfill(cp_digits)
    return {
        "scheme": "giai-96",
        "filter": filter_bits,
        "company_prefix": company_prefix,
        "asset_reference": str(asset),
        "uri": f"urn:epc:id:giai:{company_prefix}.{asset}",
    }


def _decode_giai_202(bits: _BitReader) -> dict[str, Any]:
    filter_bits = bits.read(3)
    partition = bits.read(3)
    cp_bits, _, cp_digits, _ = _GIAI_202_PARTITIONS[partition]
    cp = bits.read(cp_bits)
    asset = _read_7bit_string(bits, max_chars=18)
    company_prefix = str(cp).zfill(cp_digits)
    return {
        "scheme": "giai-202",
        "filter": filter_bits,
        "company_prefix": company_prefix,
        "asset_reference": asset,
        "uri": f"urn:epc:id:giai:{company_prefix}.{asset}",
    }


def _decode_grai_96(bits: _BitReader) -> dict[str, Any]:
    filter_bits = bits.read(3)
    partition = bits.read(3)
    cp_bits, _, cp_digits, _ = _SGTIN_PARTITIONS[partition]
    cp = bits.read(cp_bits)
    asset_type_bits = 44 - cp_bits  # remainder before serial
    asset_type = bits.read(asset_type_bits)
    serial = bits.read(38)
    company_prefix = str(cp).zfill(cp_digits)
    asset_type_str = str(asset_type)
    return {
        "scheme": "grai-96",
        "filter": filter_bits,
        "company_prefix": company_prefix,
        "asset_type": asset_type_str,
        "serial": str(serial),
        "uri": f"urn:epc:id:grai:{company_prefix}.{asset_type_str}.{serial}",
    }


def _decode_grai_170(bits: _BitReader) -> dict[str, Any]:
    filter_bits = bits.read(3)
    partition = bits.read(3)
    cp_bits, _, cp_digits, _ = _SGTIN_PARTITIONS[partition]
    cp = bits.read(cp_bits)
    asset_type_bits = 44 - cp_bits
    asset_type = bits.read(asset_type_bits)
    serial = _read_7bit_string(bits, max_chars=16)
    company_prefix = str(cp).zfill(cp_digits)
    asset_type_str = str(asset_type)
    return {
        "scheme": "grai-170",
        "filter": filter_bits,
        "company_prefix": company_prefix,
        "asset_type": asset_type_str,
        "serial": serial,
        "uri": f"urn:epc:id:grai:{company_prefix}.{asset_type_str}.{serial}",
    }


def _read_7bit_string(bits: _BitReader, *, max_chars: int) -> str:
    """Read up to ``max_chars`` 7-bit ASCII characters; stop at NUL or end-of-buffer."""
    out: list[str] = []
    for _ in range(max_chars):
        try:
            ch = bits.read(7)
        except IndexError:
            break
        if ch == 0:
            break
        out.append(chr(ch))
    return "".join(out)
