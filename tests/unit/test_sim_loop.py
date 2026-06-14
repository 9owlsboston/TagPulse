"""Unit tests for ``scripts/sim_loop.py`` (Sprint 58 Phase C).

Covers the pure-function pieces of the continuous demo simulator that don't
need an HTTP server: token bucket refill math, shift-schedule multiplier,
duration parsing, and the active-devices filter on ``SimState``.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

# scripts/ isn't on sys.path; load sim_loop as an ad-hoc module so we can
# unit-test its internals without restructuring the script into a package.
_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "sim_loop.py"
_SPEC = importlib.util.spec_from_file_location("sim_loop", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
sim_loop = importlib.util.module_from_spec(_SPEC)
sys.modules["sim_loop"] = sim_loop
_SPEC.loader.exec_module(sim_loop)


# ---------------------------------------------------------------------------
# TokenBucket
# ---------------------------------------------------------------------------


def test_token_bucket_starts_full() -> None:
    bucket = sim_loop.TokenBucket(rate_per_sec=1.0, capacity=10)
    assert bucket.tokens == 10


def test_token_bucket_consumes_available_tokens() -> None:
    bucket = sim_loop.TokenBucket(rate_per_sec=1.0, capacity=10)
    for _ in range(10):
        assert bucket.try_take(1.0) is True
    # 11th take immediately after should fail — refill hasn't accumulated yet.
    assert bucket.try_take(1.0) is False


def test_token_bucket_refills_at_configured_rate() -> None:
    bucket = sim_loop.TokenBucket(rate_per_sec=5.0, capacity=10)
    # Drain.
    for _ in range(10):
        bucket.try_take(1.0)
    assert bucket.try_take(1.0) is False
    # Simulate 1 second of elapsed time by rewinding ``last_refill``.
    bucket.last_refill = time.monotonic() - 1.0
    # Should have ~5 fresh tokens.
    taken = sum(1 for _ in range(10) if bucket.try_take(1.0))
    assert 4 <= taken <= 6  # allow a tick of wall-clock jitter


def test_token_bucket_never_exceeds_capacity() -> None:
    bucket = sim_loop.TokenBucket(rate_per_sec=100.0, capacity=10)
    # Simulate 1 hour of elapsed time — should NOT accumulate 360 000 tokens.
    bucket.last_refill = time.monotonic() - 3600.0
    bucket.try_take(0)  # trigger refill side-effect without consuming
    assert bucket.tokens == 10


# ---------------------------------------------------------------------------
# Shift schedule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        (8, 0, sim_loop._PEAK_MULTIPLIER),  # peak window centre
        (8, 25, sim_loop._PEAK_MULTIPLIER),  # within ±30 min
        (7, 35, sim_loop._PEAK_MULTIPLIER),  # within ±30 min on the other side
        (13, 15, sim_loop._PEAK_MULTIPLIER),  # second peak
        (10, 0, 1.0),  # normal daytime
        (16, 0, 1.0),  # normal daytime, no peak nearby
        (21, 0, sim_loop._OFF_HOURS_MULTIPLIER),  # late evening
        (3, 0, sim_loop._OFF_HOURS_MULTIPLIER),  # early morning
        (20, 0, sim_loop._OFF_HOURS_MULTIPLIER),  # off-hours boundary (inclusive)
    ],
)
def test_shift_multiplier(hour: int, minute: int, expected: float) -> None:
    now = datetime(2026, 6, 1, hour, minute, 0)
    assert sim_loop.shift_multiplier(now) == expected


# ---------------------------------------------------------------------------
# Duration parser
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0", 0.0),
        ("30", 30.0),  # bare integer → seconds
        ("30s", 30.0),
        ("5m", 300.0),
        ("2h", 7200.0),
        ("1d", 86400.0),
    ],
)
def test_parse_duration_valid(text: str, expected: float) -> None:
    assert sim_loop._parse_duration(text) == expected


@pytest.mark.parametrize("bad", ["", "5x", "abc", "1.5h", "-30s"])
def test_parse_duration_rejects_garbage(bad: str) -> None:
    import argparse

    with pytest.raises(argparse.ArgumentTypeError):
        sim_loop._parse_duration(bad)


# ---------------------------------------------------------------------------
# SimState.active_devices
# ---------------------------------------------------------------------------


def test_active_devices_filters_out_offline() -> None:
    state = sim_loop.SimState(devices=["a", "b", "c"], tags=["TAG0001"])
    now = time.monotonic()
    state.offline_until = {"b": now + 60.0}  # b offline for another 60s
    active = state.active_devices(now)
    assert set(active) == {"a", "c"}


def test_active_devices_includes_expired_outage() -> None:
    state = sim_loop.SimState(devices=["a", "b"], tags=["TAG0001"])
    now = time.monotonic()
    state.offline_until = {"a": now - 10.0}  # a's outage already expired
    active = state.active_devices(now)
    assert set(active) == {"a", "b"}


# ---------------------------------------------------------------------------
# Multi-tenant parsing (Sprint 59 §59.4)
# ---------------------------------------------------------------------------


def test_tenant_id_for_slug_matches_uuid5() -> None:
    """sim_loop must derive the same UUID the composer does for a slug."""
    import uuid

    for slug in ("demo-wm-dc", "demo-inv-coldchain", "demo-asset-fleet"):
        expected = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{slug}.tagpulse.local"))
        assert sim_loop._tenant_id_for_slug(slug) == expected


def test_parse_tenants_valid() -> None:
    parsed = sim_loop._parse_tenants("demo-wm-dc:KEY1, demo-inv-coldchain:KEY2")
    slugs = [slug for slug, _tid, _key in parsed]
    keys = [key for _slug, _tid, key in parsed]
    assert slugs == ["demo-wm-dc", "demo-inv-coldchain"]
    assert keys == ["KEY1", "KEY2"]
    # tenant_id is derived deterministically from the slug.
    assert parsed[0][1] == sim_loop._tenant_id_for_slug("demo-wm-dc")


def test_parse_tenants_keeps_colons_in_key() -> None:
    """Only the first colon splits slug from key — keys may contain colons."""
    parsed = sim_loop._parse_tenants("demo-wm-dc:tp:abc:123")
    assert parsed[0][0] == "demo-wm-dc"
    assert parsed[0][2] == "tp:abc:123"


@pytest.mark.parametrize("bad", ["", "no-colon-here", "demo-wm-dc:", ":KEY", "  "])
def test_parse_tenants_rejects_garbage(bad: str) -> None:
    with pytest.raises(ValueError):
        sim_loop._parse_tenants(bad)


def test_parse_tenants_rejects_duplicate_slug() -> None:
    with pytest.raises(ValueError):
        sim_loop._parse_tenants("demo-wm-dc:KEY1,demo-wm-dc:KEY2")


# ---------------------------------------------------------------------------
# Aggregate rate split (Sprint 59 §59.4 D4)
# ---------------------------------------------------------------------------


def test_split_rate_preserves_aggregate() -> None:
    # The whole point: per-tenant × N == aggregate (ceiling preserved).
    assert sim_loop._split_rate(200, 1) == 200
    assert sim_loop._split_rate(200, 2) == 100
    assert sim_loop._split_rate(200, 3) * 3 == pytest.approx(200)


def test_split_rate_rejects_zero_tenants() -> None:
    with pytest.raises(ValueError):
        sim_loop._split_rate(200, 0)


# ---------------------------------------------------------------------------
# Heartbeat (Sprint 59 §59.7)
# ---------------------------------------------------------------------------


def test_devices_needing_heartbeat_warms_all_at_startup() -> None:
    """Empty last_emit → every active device qualifies (cold-start warm-up)."""
    state = sim_loop.SimState(devices=["a", "b", "c"], tags=["TAG0001"])
    now = time.monotonic()
    stale = sim_loop._devices_needing_heartbeat(state, now, sim_loop._HEARTBEAT_INTERVAL_SECONDS)
    assert set(stale) == {"a", "b", "c"}


def test_devices_needing_heartbeat_excludes_recent_and_offline() -> None:
    state = sim_loop.SimState(devices=["a", "b", "c"], tags=["TAG0001"])
    now = time.monotonic()
    interval = sim_loop._HEARTBEAT_INTERVAL_SECONDS
    state.last_emit = {
        "a": now - 1.0,  # just emitted → fresh, skip
        "b": now - interval - 5.0,  # stale → needs heartbeat
        # c never emitted → stale
    }
    state.offline_until = {"c": now + 60.0}  # c offline → excluded despite stale
    stale = sim_loop._devices_needing_heartbeat(state, now, interval)
    assert set(stale) == {"b"}


def test_heartbeat_interval_under_online_window() -> None:
    """Heartbeat must fire well before the Dashboard's 5-min online window."""
    assert sim_loop._HEARTBEAT_INTERVAL_SECONDS < 5 * 60


# ---------------------------------------------------------------------------
# TenantRuntime
# ---------------------------------------------------------------------------


def test_tenant_runtime_holds_independent_state() -> None:
    a = sim_loop.TenantRuntime(
        slug="demo-wm-dc",
        tenant_id="id-a",
        api_key="KEY1",
        state=sim_loop.SimState(devices=[], tags=["TAG0001"]),
        bucket=sim_loop.TokenBucket(rate_per_sec=1.0, capacity=10),
        base_rate_per_min=100.0,
    )
    b = sim_loop.TenantRuntime(
        slug="demo-inv-coldchain",
        tenant_id="id-b",
        api_key="KEY2",
        state=sim_loop.SimState(devices=[], tags=["TAG0001"]),
        bucket=sim_loop.TokenBucket(rate_per_sec=1.0, capacity=10),
        base_rate_per_min=100.0,
    )
    a.state.total_reads += 5
    assert a.state.total_reads == 5
    assert b.state.total_reads == 0  # independent state
    assert a.state is not b.state
