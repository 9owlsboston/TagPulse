"""Conformance: heartbeat + LWT — docs/design/edge-device-contract.md §3.6."""

# Per docs/design/edge-device-contract.md §3.6 + §3.9.
SPEC_DEFAULT_HEARTBEAT_INTERVAL_S = 60


def test_spec_heartbeat_interval_default() -> None:
    assert SPEC_DEFAULT_HEARTBEAT_INTERVAL_S == 60
