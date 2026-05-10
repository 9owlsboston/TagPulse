"""Sprint 28 C3 — dead_letter_events.source column wiring.

Verifies the ORM model has the new column with the right default and
that the two existing in-tree writers (AsyncEventBus and
``TimescaleTagReadRepository.record_rejection``) populate it correctly.
The migration's actual DDL is exercised by ``make migration-check``
against a live TimescaleDB.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tagpulse.events.async_bus import AsyncEventBus
from tagpulse.events.protocol import Event, Topic
from tagpulse.models.database import DeadLetterEventModel


def test_model_has_source_column_with_default() -> None:
    col = DeadLetterEventModel.__table__.columns["source"]
    assert col.nullable is False
    assert col.default.arg == "event_bus"  # SQLAlchemy ColumnDefault
    # server_default lets existing rows backfill via the migration.
    assert "event_bus" in str(col.server_default.arg)


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[DeadLetterEventModel] = []

    def add(self, row: DeadLetterEventModel) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        return None

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


def _factory(session: _FakeSession):
    def make() -> _FakeSession:
        return session

    return make


@pytest.mark.asyncio
async def test_async_bus_persist_sets_source_event_bus() -> None:
    session = _FakeSession()
    bus = AsyncEventBus(dead_letter_factory=_factory(session))
    event = Event(
        id=uuid4(),
        topic=Topic.TAG_READ_CREATED,
        timestamp=datetime.now(UTC),
        payload={"tenant_id": str(uuid4()), "device_id": str(uuid4())},
    )
    await bus._persist_dead_letter(  # noqa: SLF001
        Topic.TAG_READ_CREATED, event, "boom"
    )
    assert len(session.added) == 1
    assert session.added[0].source == "event_bus"
    assert session.added[0].error_message == "boom"


def test_record_rejection_payload_sets_source_tag_read_rejected() -> None:
    """Inspect the source kwarg passed to DeadLetterEventModel without
    needing a live session — easier than wiring async DB fakes for this
    one assertion."""
    from tagpulse.repositories.timescaledb import tag_reads as tr_mod

    captured: list[DeadLetterEventModel] = []

    class _S:
        def add(self, row: DeadLetterEventModel) -> None:
            captured.append(row)

        async def flush(self) -> None:
            return None

    repo = tr_mod.TimescaleTagReadRepository.__new__(tr_mod.TimescaleTagReadRepository)
    repo._session = _S()  # type: ignore[attr-defined]

    from tagpulse.models.schemas import TagReadCreate

    read = TagReadCreate(
        device_id=uuid4(),
        tag_id="E2001234567890ABCDEF1234",
        timestamp=datetime.now(UTC),
    )
    asyncio.run(repo.record_rejection(uuid4(), read, "clock_too_old"))
    assert len(captured) == 1
    assert captured[0].source == "tag_read_rejected"
    assert captured[0].error_message == "clock_too_old"
