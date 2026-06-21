"""Unit tests for the leg env + SLA summarizer (Sprint 72, ADR-034 Phase 2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tagpulse.services.consolidation import SlaConfig
from tagpulse.services.legs import EnvSample, summarize_leg_env

T0 = datetime(2026, 6, 20, 8, 0, 0, tzinfo=UTC)


def _s(offset_s: int, temp: float | None = None, hum: float | None = None) -> EnvSample:
    return EnvSample(time=T0 + timedelta(seconds=offset_s), temperature_c=temp, humidity_pct=hum)


def test_envelope_without_sla() -> None:
    out = summarize_leg_env([_s(0, 3.0, 50), _s(60, 5.0, 70), _s(120, 4.0, 60)], None)
    assert out.temp_min_c == 3.0
    assert out.temp_max_c == 5.0
    assert out.temp_mean_c == 4.0
    assert out.humidity_min == 50
    assert out.humidity_max == 70
    # No SLA → no scoring.
    assert out.excursion_s is None
    assert out.in_range_pct is None
    assert out.sla_breached is None


def test_all_in_range() -> None:
    sla = SlaConfig(temp_min_c=2.0, temp_max_c=8.0, excursion_tolerance_s=0)
    out = summarize_leg_env([_s(0, 3.0), _s(60, 4.0), _s(120, 5.0)], sla)
    assert out.in_range_pct == 100.0
    assert out.excursion_s == 0
    assert out.sla_breached is False


def test_excursion_breach() -> None:
    # Out of range at t=60..180 (120s contiguous) → breach when tolerance < 120.
    sla = SlaConfig(temp_min_c=2.0, temp_max_c=8.0, excursion_tolerance_s=60)
    samples = [_s(0, 4.0), _s(60, 9.0), _s(120, 9.5), _s(180, 9.1), _s(240, 4.0)]
    out = summarize_leg_env(samples, sla)
    assert out.temp_max_c == 9.5
    assert out.excursion_s == 120  # 180 - 60
    assert out.sla_breached is True
    assert out.in_range_pct == 40.0  # 2 of 5 in range


def test_excursion_within_tolerance_not_breached() -> None:
    sla = SlaConfig(temp_min_c=2.0, temp_max_c=8.0, excursion_tolerance_s=120)
    samples = [_s(0, 4.0), _s(60, 9.0), _s(120, 9.5), _s(180, 9.1), _s(240, 4.0)]
    out = summarize_leg_env(samples, sla)
    assert out.excursion_s == 120
    assert out.sla_breached is False  # 120 is not > 120


def test_humidity_bound() -> None:
    sla = SlaConfig(humidity_max=80.0)
    out = summarize_leg_env([_s(0, hum=70), _s(60, hum=95), _s(120, hum=60)], sla)
    assert out.humidity_max == 95
    assert out.in_range_pct is not None
    assert out.sla_breached is not None


def test_unbounded_sla_is_envelope_only() -> None:
    # SlaConfig with only a tolerance (no envelope bounds) → no scoring.
    out = summarize_leg_env([_s(0, 4.0), _s(60, 99.0)], SlaConfig(excursion_tolerance_s=30))
    assert out.temp_max_c == 99.0
    assert out.in_range_pct is None
    assert out.sla_breached is None


def test_none_readings_treated_in_range() -> None:
    sla = SlaConfig(temp_min_c=2.0, temp_max_c=8.0)
    out = summarize_leg_env([_s(0, None), _s(60, 4.0)], sla)
    assert out.in_range_pct == 100.0
    assert out.temp_min_c == 4.0
