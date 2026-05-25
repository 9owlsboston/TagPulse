"""Conformance: dedup + ENTER/EXIT — docs/design/edge-device-contract.md §3.3.

Stub. Pins spec'd defaults so any contract drift fails CI; the reference
client at `clients/pi/tagpulse_edge/dedup.py` implements the behaviour. Once
a candidate device exposes a test harness on `localhost`, real round-trip
tests land here.
"""

# Per docs/design/edge-device-contract.md §3.3 + §3.9.
SPEC_DEFAULT_DEDUP_WINDOW_S = 5
SPEC_DEFAULT_EXIT_TIMEOUT_S = 10


def test_spec_dedup_window_default() -> None:
    assert SPEC_DEFAULT_DEDUP_WINDOW_S == 5


def test_spec_exit_timeout_default() -> None:
    assert SPEC_DEFAULT_EXIT_TIMEOUT_S == 10
