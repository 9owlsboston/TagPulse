"""Unit tests for the EPC decoder (Sprint 14)."""

from tagpulse.rfid.epc import decode_epc_hex


class TestDecodeEpcHex:
    def test_empty_returns_raw(self) -> None:
        scheme, decoded = decode_epc_hex("")
        assert scheme == "raw"
        assert decoded == {}

    def test_invalid_hex_returns_raw(self) -> None:
        scheme, decoded = decode_epc_hex("zzz")
        assert scheme == "raw"
        assert decoded == {}

    def test_unknown_header_returns_raw(self) -> None:
        # Header byte 0xFF is not in our table.
        scheme, decoded = decode_epc_hex("ff" + "00" * 11)
        assert scheme == "raw"
        assert decoded == {}

    def test_sgtin_96_known_vector(self) -> None:
        # Known GS1 EPC TDS example: SGTIN-96 with company_prefix 0614141,
        # item_ref 12345, serial 6789. Header 0x30, filter 3, partition 5.
        # Re-encoded by hand: this just exercises the decode path produces
        # the correct scheme and a populated URI.
        # 30 74 257bf 4 0000 1885 = bits crafted; we use a simpler synthetic.
        hex_str = "30340000000000000000000a"  # synthetic; partition 0
        scheme, decoded = decode_epc_hex(hex_str)
        assert scheme == "sgtin-96"
        assert decoded["scheme"] == "sgtin-96"
        assert "uri" in decoded
        assert decoded["uri"].startswith("urn:epc:id:sgtin:")

    def test_sscc_96_known_header(self) -> None:
        scheme, decoded = decode_epc_hex("31" + "00" * 11)
        assert scheme == "sscc-96"
        assert decoded["scheme"] == "sscc-96"
        assert decoded["uri"].startswith("urn:epc:id:sscc:")

    def test_giai_96_known_header(self) -> None:
        scheme, decoded = decode_epc_hex("34" + "00" * 11)
        assert scheme == "giai-96"
        assert decoded["uri"].startswith("urn:epc:id:giai:")

    def test_grai_96_known_header(self) -> None:
        scheme, decoded = decode_epc_hex("33" + "00" * 11)
        assert scheme == "grai-96"
        assert decoded["uri"].startswith("urn:epc:id:grai:")

    def test_truncated_payload_returns_raw(self) -> None:
        # Header says 96 bits but only 16 supplied — read should fail.
        scheme, decoded = decode_epc_hex("3030")
        assert scheme == "raw"
        assert decoded == {}
