"""Sprint 13b — MetricsRepository factory + per-backend SQL dialect."""

from __future__ import annotations

import pytest

from tagpulse.core import config as cfg_module
from tagpulse.repositories.metrics import (
    PostgresMetricsRepository,
    TimescaleMetricsRepository,
    get_metrics_repository,
)


def _sql(repo_cls: type) -> str:
    return str(repo_cls._SQL).lower()  # type: ignore[attr-defined]


def test_timescale_impl_uses_time_bucket() -> None:
    sql = _sql(TimescaleMetricsRepository)
    assert "time_bucket" in sql
    assert "date_trunc" not in sql


def test_postgres_impl_uses_date_trunc() -> None:
    sql = _sql(PostgresMetricsRepository)
    assert "date_trunc" in sql
    assert "time_bucket" not in sql


def test_factory_picks_timescale_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg_module.settings, "database_backend", "timescale")
    assert isinstance(get_metrics_repository(session=None), TimescaleMetricsRepository)  # type: ignore[arg-type]


def test_factory_picks_postgres_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg_module.settings, "database_backend", "postgres")
    assert isinstance(get_metrics_repository(session=None), PostgresMetricsRepository)  # type: ignore[arg-type]


def test_both_impls_share_identical_select_columns() -> None:
    """The whole point of the seam — same row shape on both backends."""
    ts = _sql(TimescaleMetricsRepository)
    pg = _sql(PostgresMetricsRepository)
    for col in ("bucket_start", "reader_id", "read_count"):
        assert col in ts
        assert col in pg
