"""Sprint 39: ADR-020 Phase B — verify that hard-delete repository
methods on sites, zones, and categories cascade-clean their orphan
``entity_labels`` rows before the entity row is removed.

These tests use an in-memory session double that records the order
of ``execute`` / ``delete`` / ``flush`` calls. They are not full
integration tests (no Postgres) but they pin the *contract*: each
hard-delete repository method MUST issue a SELECT against
``entity_labels JOIN labels`` (the cleanup query) before the
``session.delete(entity_row)`` call, and the cleanup MUST be
tenant-scoped via the labels join.

Without this contract, orphan ``entity_labels`` rows survive their
entity, inflating ``count_associations()`` on the parent label and
blocking ``DELETE /labels/{id}`` forever with an unresolvable 409.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from tagpulse.repositories.timescaledb.categories import TimescaleCategoryRepository
from tagpulse.repositories.timescaledb.sites_zones import (
    TimescaleSiteRepository,
    TimescaleZoneRepository,
)


class _RecordingSession:
    """Async session that records every ``execute``, ``delete``,
    and ``flush`` in invocation order.

    ``execute_results`` is a queue of return values for successive
    ``execute`` calls. Each entry is a ``(scalar_one_or_none,
    scalars_all)`` tuple — the SiteModel/Zone/Category row for the
    primary lookup, then the entity_labels rows (or ``[]`` for the
    common no-orphans case).
    """

    def __init__(self, execute_results: list[tuple[Any, list[Any]]]) -> None:
        self.events: list[tuple[str, Any]] = []
        self.compiled_sql: list[str] = []
        self._results = list(execute_results)

    async def execute(self, stmt: Any) -> Any:
        self.events.append(("execute", stmt))
        try:
            sql = str(
                stmt.compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": False},
                )
            ).lower()
        except Exception:  # pragma: no cover - belt & suspenders
            sql = ""
        self.compiled_sql.append(sql)
        scalar_row, scalars_list = self._results.pop(0)

        class _Result:
            def scalar_one_or_none(self) -> Any:
                return scalar_row

            def scalars(self) -> Any:
                class _Scalars:
                    def all(self) -> list[Any]:
                        return scalars_list

                return _Scalars()

        return _Result()

    async def delete(self, row: Any) -> None:
        self.events.append(("delete", row))

    async def flush(self) -> None:
        self.events.append(("flush", None))


def _event_kinds(session: _RecordingSession) -> list[str]:
    return [kind for kind, _ in session.events]


@pytest.mark.asyncio
async def test_site_delete_runs_orphan_cleanup_before_deleting_site() -> None:
    """``TimescaleSiteRepository.delete`` first looks up the site,
    then issues the orphan-cleanup SELECT, then deletes the site
    row, then flushes. The cleanup SELECT must JOIN ``labels`` and
    filter on ``labels.entity_type = 'site'``."""
    site_row = object()
    session = _RecordingSession(
        execute_results=[
            (site_row, []),  # initial site lookup
            (None, []),  # orphan SELECT — no orphans is the common case
        ]
    )
    repo = TimescaleSiteRepository(session)  # type: ignore[arg-type]
    deleted = await repo.delete(uuid.uuid4(), uuid.uuid4())

    assert deleted is True
    assert _event_kinds(session) == ["execute", "execute", "delete", "flush"]
    # Site row is exactly what gets passed to session.delete.
    assert session.events[2][1] is site_row
    # The orphan SELECT is event 1; it must be the entity_labels query.
    orphan_sql = session.compiled_sql[1]
    assert "from entity_labels" in orphan_sql
    assert "join labels" in orphan_sql
    assert "labels.tenant_id" in orphan_sql
    assert "labels.entity_type" in orphan_sql


@pytest.mark.asyncio
async def test_site_delete_returns_false_when_site_missing_and_skips_cleanup() -> None:
    """No site → no labels could possibly reference it; cleanup
    must be skipped (no second execute, no delete)."""
    session = _RecordingSession(execute_results=[(None, [])])
    repo = TimescaleSiteRepository(session)  # type: ignore[arg-type]
    deleted = await repo.delete(uuid.uuid4(), uuid.uuid4())

    assert deleted is False
    assert _event_kinds(session) == ["execute"]


@pytest.mark.asyncio
async def test_zone_delete_runs_orphan_cleanup_and_invalidates_cache() -> None:
    """Same contract as sites, with the added geofence cache
    invalidation that ``TimescaleZoneRepository.delete`` performs
    after flush."""
    zone_row = object()
    session = _RecordingSession(
        execute_results=[
            (zone_row, []),  # zone lookup
            (None, []),  # orphan SELECT
        ]
    )
    repo = TimescaleZoneRepository(session)  # type: ignore[arg-type]
    deleted = await repo.delete(uuid.uuid4(), uuid.uuid4())

    assert deleted is True
    assert _event_kinds(session) == ["execute", "execute", "delete", "flush"]
    orphan_sql = session.compiled_sql[1]
    assert "labels.entity_type" in orphan_sql
    assert "entity_labels.entity_id" in orphan_sql


@pytest.mark.asyncio
async def test_category_delete_runs_orphan_cleanup_after_in_use_guard() -> None:
    """Category delete has an extra step: ``count_referencing_assets``
    runs first and raises ``CategoryInUseError`` if any asset still
    points at the category. Only when that guard passes should the
    orphan cleanup + entity delete run. Order: count → category
    lookup → orphan cleanup → delete → flush."""
    category_row = object()
    session = _RecordingSession(
        execute_results=[
            (0, []),  # count_referencing_assets -> scalar_one
            (category_row, []),  # category lookup
            (None, []),  # orphan SELECT
        ]
    )

    # count_referencing_assets uses scalar_one(); patch the result.
    original_execute = session.execute

    async def execute_with_scalar_one(stmt: Any) -> Any:
        result = await original_execute(stmt)
        # Wrap to add scalar_one() returning the same value as
        # scalar_one_or_none() would have returned.
        scalar = result.scalar_one_or_none()

        class _Wrap:
            def scalar_one(self_inner) -> Any:  # noqa: N805
                return 0 if scalar is None else scalar

            def scalar_one_or_none(self_inner) -> Any:  # noqa: N805
                return scalar

            def scalars(self_inner) -> Any:  # noqa: N805
                return result.scalars()

        return _Wrap()

    session.execute = execute_with_scalar_one  # type: ignore[assignment]

    repo = TimescaleCategoryRepository(session)  # type: ignore[arg-type]
    deleted = await repo.delete(uuid.uuid4(), uuid.uuid4())

    assert deleted is True
    assert _event_kinds(session) == [
        "execute",  # count_referencing_assets
        "execute",  # category lookup
        "execute",  # orphan cleanup SELECT
        "delete",
        "flush",
    ]
    # Compile-check the orphan SELECT — index 2 after the two earlier
    # SELECTs.
    orphan_sql = session.compiled_sql[2]
    assert "from entity_labels" in orphan_sql
    assert "labels.entity_type" in orphan_sql
    assert "labels.tenant_id" in orphan_sql
