"""Conformance: offline buffer — docs/design/edge-device-contract.md §3.7.

Stub. Pins spec'd defaults; real round-trip lands when a device exposes a
test harness.
"""

# Per docs/design/edge-device-contract.md §3.7 + §3.9.
SPEC_DEFAULT_BUFFER_MAX_BYTES = 100_000_000
SPEC_DEFAULT_BUFFER_MAX_AGE_S = 86_400


def test_spec_buffer_max_bytes_default() -> None:
    assert SPEC_DEFAULT_BUFFER_MAX_BYTES == 100_000_000


def test_spec_buffer_max_age_default() -> None:
    assert SPEC_DEFAULT_BUFFER_MAX_AGE_S == 24 * 3600
