"""Sprint 21: TTL caches + deprecation sunset (ADR-015 §5, §6).

Covers the backend portion of Sprint 21:

* :class:`tagpulse.core.ttl_cache.TTLCache` semantics (get/set, expiry,
  FIFO eviction, invalidation, clear).
* :data:`SUBJECT_KINDS_CACHE` invalidation by ``PATCH /tenant/config``
  (the ADR-015 §5 carry-over: "either Redis pub/sub or short TTL"; we
  picked short TTL).
* :data:`LATEST_TELEMETRY_CACHE` coalescing on ``GET /assets/{id}`` —
  second call with ``with_latest_telemetry=True`` does not re-hit the
  readings repo.
* The 410 Gone response on the legacy
  ``GET /telemetry-models/{device_type}`` path (Sprint 21 sunset of
  the Sprint 19 301 redirect) lives in
  ``test_sprint19_subject_telemetry.py`` so the redirect tests stay
  co-located with their replacement.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from tagpulse.api.services.asset_service import AssetService
from tagpulse.core.audit import AuditLogger
from tagpulse.core.telemetry_caches import (
    LATEST_TELEMETRY_CACHE,
    SUBJECT_KINDS_CACHE,
    invalidate_subject_kinds,
)
from tagpulse.core.ttl_cache import TTLCache
from tagpulse.models.schemas import (
    AssetResponse,
    LatestTelemetryEntry,
)

# -- TTLCache primitives --


def test_ttl_cache_set_then_get_returns_value() -> None:
    c: TTLCache[str, int] = TTLCache(ttl_seconds=30.0, maxsize=4)
    c.set("k", 1)
    assert c.get("k") == 1


def test_ttl_cache_get_missing_returns_none() -> None:
    c: TTLCache[str, int] = TTLCache(ttl_seconds=30.0, maxsize=4)
    assert c.get("absent") is None


def test_ttl_cache_expires_after_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """An entry past its TTL must read as missing and be self-evicted."""
    fake_now = [0.0]

    def _clock() -> float:
        return fake_now[0]

    c: TTLCache[str, int] = TTLCache(ttl_seconds=30.0, maxsize=4)
    monkeypatch.setattr(c, "_clock", _clock)

    c.set("k", 1)
    fake_now[0] = 29.999
    assert c.get("k") == 1
    fake_now[0] = 30.001
    assert c.get("k") is None
    # second read must not raise (entry self-evicted by the first stale get)
    assert c.get("k") is None


def test_ttl_cache_evicts_oldest_on_overflow() -> None:
    c: TTLCache[str, int] = TTLCache(ttl_seconds=30.0, maxsize=2)
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)  # evicts "a"
    assert c.get("a") is None
    assert c.get("b") == 2
    assert c.get("c") == 3


def test_ttl_cache_invalidate_drops_entry() -> None:
    c: TTLCache[str, int] = TTLCache(ttl_seconds=30.0, maxsize=4)
    c.set("k", 1)
    c.invalidate("k")
    assert c.get("k") is None
    # idempotent
    c.invalidate("absent")


def test_ttl_cache_clear_removes_all() -> None:
    c: TTLCache[str, int] = TTLCache(ttl_seconds=30.0, maxsize=4)
    c.set("a", 1)
    c.set("b", 2)
    c.clear()
    assert c.get("a") is None
    assert c.get("b") is None


def test_ttl_cache_rejects_non_positive_ttl() -> None:
    with pytest.raises(ValueError):
        TTLCache(ttl_seconds=0.0, maxsize=4)
    with pytest.raises(ValueError):
        TTLCache(ttl_seconds=-1.0, maxsize=4)


def test_ttl_cache_rejects_non_positive_maxsize() -> None:
    with pytest.raises(ValueError):
        TTLCache(ttl_seconds=30.0, maxsize=0)


# -- SUBJECT_KINDS_CACHE invalidation --


@pytest.fixture(autouse=True)
def _clear_module_caches() -> None:
    SUBJECT_KINDS_CACHE.clear()
    LATEST_TELEMETRY_CACHE.clear()


def test_invalidate_subject_kinds_drops_only_target_tenant() -> None:
    t1 = uuid4()
    t2 = uuid4()
    SUBJECT_KINDS_CACHE.set(t1, ("device", "lot"))
    SUBJECT_KINDS_CACHE.set(t2, ("device",))
    invalidate_subject_kinds(t1)
    assert SUBJECT_KINDS_CACHE.get(t1) is None
    assert SUBJECT_KINDS_CACHE.get(t2) == ("device",)


def test_invalidate_subject_kinds_idempotent_for_unknown_tenant() -> None:
    invalidate_subject_kinds(uuid4())  # must not raise


# -- LATEST_TELEMETRY_CACHE coalesces AssetService.get_asset --


class _FakeAssetRepo:
    def __init__(self, asset: AssetResponse) -> None:
        self._asset = asset

    async def get(self, tenant_id: UUID, asset_id: UUID) -> AssetResponse:
        return self._asset


class _FakeBindingRepo:
    pass  # AssetService doesn't use this in get_asset


class _FakeTenantRepo:
    def __init__(self, kinds: list[str]) -> None:
        self._kinds = kinds
        self.calls = 0

    async def get_telemetry_subject_kinds(self, tenant_id: UUID) -> list[str]:
        self.calls += 1
        return list(self._kinds)


class _FakeReadingsRepo:
    def __init__(self, latest: list[LatestTelemetryEntry]) -> None:
        self._latest = latest
        self.calls = 0

    async def latest_per_metric(self, **kwargs: Any) -> list[LatestTelemetryEntry]:
        self.calls += 1
        return list(self._latest)


def _make_asset(tenant_id: UUID, asset_id: UUID) -> AssetResponse:
    return AssetResponse(
        id=asset_id,
        tenant_id=tenant_id,
        name="A1",
        status="active",
        owner=None,
        external_ref=None,
        parent_asset_id=None,
        category_id=uuid4(),
        metadata=None,
        current_zone_id=None,
        latest_telemetry=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


class _NoopAudit(AuditLogger):
    def __init__(self) -> None:  # type: ignore[no-untyped-def]
        # Bypass the parent's ``__init__`` so we don't need a real session.
        pass

    async def log(self, *args: Any, **kwargs: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_get_asset_caches_latest_telemetry_for_30s() -> None:
    """Two consecutive ``get_asset`` calls inside the TTL window must
    only hit the readings repo once."""
    tenant = uuid4()
    asset_id = uuid4()
    asset = _make_asset(tenant, asset_id)
    readings = _FakeReadingsRepo(
        latest=[
            LatestTelemetryEntry(
                metric_name="temperature_c",
                metric_value=4.0,
                unit="C",
                timestamp=datetime.now(UTC),
                source="tag",
            )
        ]
    )
    tenant_repo = _FakeTenantRepo(kinds=["device", "asset"])
    svc = AssetService(
        asset_repo=_FakeAssetRepo(asset),  # type: ignore[arg-type]
        binding_repo=_FakeBindingRepo(),  # type: ignore[arg-type]
        audit=_NoopAudit(),
        telemetry_readings_repo=readings,  # type: ignore[arg-type]
        tenant_repo=tenant_repo,  # type: ignore[arg-type]
    )

    a1 = await svc.get_asset(tenant, asset_id, with_latest_telemetry=True)
    a2 = await svc.get_asset(tenant, asset_id, with_latest_telemetry=True)
    assert a1 is not None
    assert a2 is not None
    assert a1.latest_telemetry is not None
    assert a2.latest_telemetry is not None
    # second call served from cache
    assert readings.calls == 1
    # subject_kinds also cached
    assert tenant_repo.calls == 1


@pytest.mark.asyncio
async def test_get_asset_re_fetches_after_cache_invalidation() -> None:
    """Invalidating the per-tenant ``LATEST_TELEMETRY_CACHE`` entry
    forces a fresh repo round-trip on the next call."""
    tenant = uuid4()
    asset_id = uuid4()
    asset = _make_asset(tenant, asset_id)
    readings = _FakeReadingsRepo(latest=[])
    tenant_repo = _FakeTenantRepo(kinds=["device", "asset"])
    svc = AssetService(
        asset_repo=_FakeAssetRepo(asset),  # type: ignore[arg-type]
        binding_repo=_FakeBindingRepo(),  # type: ignore[arg-type]
        audit=_NoopAudit(),
        telemetry_readings_repo=readings,  # type: ignore[arg-type]
        tenant_repo=tenant_repo,  # type: ignore[arg-type]
    )

    await svc.get_asset(tenant, asset_id, with_latest_telemetry=True)
    LATEST_TELEMETRY_CACHE.invalidate((tenant, "asset", asset_id))
    await svc.get_asset(tenant, asset_id, with_latest_telemetry=True)
    assert readings.calls == 2


@pytest.mark.asyncio
async def test_get_asset_skips_cache_when_kind_not_opted_in() -> None:
    """Asset-scoped telemetry not in the tenant's opt-in list must
    skip the readings repo entirely; the cache must remain empty."""
    tenant = uuid4()
    asset_id = uuid4()
    asset = _make_asset(tenant, asset_id)
    readings = _FakeReadingsRepo(latest=[])
    tenant_repo = _FakeTenantRepo(kinds=["device"])  # asset not opted in
    svc = AssetService(
        asset_repo=_FakeAssetRepo(asset),  # type: ignore[arg-type]
        binding_repo=_FakeBindingRepo(),  # type: ignore[arg-type]
        audit=_NoopAudit(),
        telemetry_readings_repo=readings,  # type: ignore[arg-type]
        tenant_repo=tenant_repo,  # type: ignore[arg-type]
    )
    fetched = await svc.get_asset(tenant, asset_id, with_latest_telemetry=True)
    assert fetched is not None
    assert fetched.latest_telemetry is None
    assert readings.calls == 0
    assert LATEST_TELEMETRY_CACHE.get((tenant, "asset", asset_id)) is None
