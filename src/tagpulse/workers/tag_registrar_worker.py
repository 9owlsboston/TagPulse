"""Tag registrar worker (Sprint 50 Phase D — implements [ADR-028]
(../../docs/adr/028-tags-as-first-class-entity.md) §"Hot-path interaction"
and §"Gating: tag_known on tag_reads").

The MQTT ingest path is **forbidden** from reading or writing the
``tags`` table — it writes ``tag_reads`` with ``tag_known = NULL`` and
moves on. This worker is the sole consumer that closes that loop:

- **D1** — drains ``tag_reads WHERE tag_known IS NULL`` in small
  batches, joins to ``tags`` per ``(tenant_id, epc_hex)``, and writes
  back ``tag_known = TRUE`` (tag present + status ∈ ``{registered,
  active}``) or ``tag_known = FALSE`` (tag absent, terminal status,
  or the read had no EPC at all — e.g. raw RSSI-only events).
- **D2** — for every matching read, populates the registry's
  observational columns: ``first_seen_at`` if NULL, ``last_seen_at``
  to the most recent observed read timestamp in the batch, and
  promotes ``status = 'registered' → 'active'`` on the first
  matching read (ADR 028 OQ 3 onboarding contract; the
  ``_OPERATOR_TRANSITIONS`` table in :mod:`tagpulse.services.tags`
  intentionally omits this edge so admins cannot manually promote a
  tag without a corresponding read).

The classifier ``classify_reads`` is pure — it takes a list of
``_ReadRow`` snapshots + a list of ``TagModel`` rows and emits a
``_Classification`` describing the three write sets (``tag_known``
TRUE id list, FALSE id list, in-place tag mutations). The worker's
``run_once`` is the thin I/O wrapper that fetches inputs, calls the
classifier, applies the mutations, and commits. Tests cover the
classifier; the I/O wrapper is exercised by a single smoke test.

SLI: registrar worker lag p95 < 10 s
(:file:`docs/roadmap.md` Sprint 50 risks). At the default 1 s tick
with ``batch_size=500`` the worker drains representative ingest
rates well inside that envelope. When a batch fills (work pending),
the loop skips the sleep and immediately re-runs — convergent
behaviour under burst.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tagpulse.core.otel_metrics import (
    tag_registrar_processed_counter,
    tag_registrar_promoted_counter,
)
from tagpulse.models.database import TagModel, TagReadModel
from tagpulse.services.tags import normalize_epc_hex

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

logger = logging.getLogger(__name__)


# Statuses that count as "tenant owns this EPC" for the purposes of
# tag_known. Mirrors the ADR 028 §"Gating" definition exactly. Terminal
# states (retired, defective, transferred_out) deliberately classify
# FALSE — operators want unmistakable "I retired this last week and a
# read snuck through" signal, not silent re-acceptance.
_OWNING_STATUSES: frozenset[str] = frozenset({"registered", "active"})


@dataclass(frozen=True)
class _ReadRow:
    """Snapshot of the columns the worker needs from ``tag_reads``.

    ``epc_hex`` is the registry join key (matches ``tags.epc_hex``). It is
    deliberately **not** ``tag_reads.epc`` — that column holds the decoded
    EPC **URI** for schemed tags (e.g. ``urn:epc:id:sgtin:…``), which never
    equals the hex the registry is keyed on. Matching on ``epc`` silently
    classified every decodable (SGTIN/SSCC/…) read as unknown.
    """

    id: uuid.UUID
    tenant_id: uuid.UUID
    epc_hex: str | None
    timestamp: datetime


@dataclass
class _TagUpdate:
    """In-place mutation the worker will flush on the loaded ``TagModel``.

    Carried as a value object (not as direct ORM mutation inside the
    classifier) so the classifier stays pure and unit-testable without
    a session.
    """

    tag_id: uuid.UUID
    new_status: str | None  # None = no status change
    new_first_seen_at: datetime | None  # None = leave as-is
    new_last_seen_at: datetime  # always written (always == observed max)


@dataclass
class _Classification:
    known_true_read_ids: list[uuid.UUID] = field(default_factory=list)
    known_false_read_ids: list[uuid.UUID] = field(default_factory=list)
    tag_updates: list[_TagUpdate] = field(default_factory=list)
    promoted_count: int = 0  # subset of tag_updates with status change


def classify_reads(
    reads: Sequence[_ReadRow],
    tags: Iterable[TagModel],
) -> _Classification:
    """Pure classifier: bucket reads → tag_known TRUE/FALSE and derive
    the per-tag mutations needed for D2.

    Inputs:
      * ``reads`` — the batch of unprocessed ``tag_reads`` rows.
      * ``tags`` — every ``TagModel`` row matching some
        ``(tenant_id, normalize_epc_hex(epc))`` key in the batch. The
        caller is responsible for fetching exactly this set; missing
        rows are interpreted as "EPC not in registry → FALSE".

    Reads with ``epc_hex IS NULL`` short-circuit to FALSE without
    consulting the tag set — these are non-EPC telemetry reads
    (e.g. raw RSSI-only legacy readers) that conceptually can never
    be "known" because there is no identity to match against.

    Status semantics:
      * ``registered`` / ``active`` → TRUE. ``registered`` is also
        promoted to ``active`` in the same pass (D2).
      * ``retired`` / ``defective`` / ``transferred_out`` → FALSE.
        These are terminal owning states from the operator's POV
        but they are not *current* ownership.
    """
    result = _Classification()
    if not reads:
        return result

    # Index tags by the natural key the classifier joins on.
    tag_by_key: dict[tuple[uuid.UUID, str], TagModel] = {(t.tenant_id, t.epc_hex): t for t in tags}

    # Group reads by (tenant_id, normalised epc). Reads with epc IS NULL
    # bypass the join entirely.
    grouped: dict[tuple[uuid.UUID, str], list[_ReadRow]] = defaultdict(list)
    for read in reads:
        if read.epc_hex is None:
            result.known_false_read_ids.append(read.id)
            continue
        key = (read.tenant_id, normalize_epc_hex(read.epc_hex))
        grouped[key].append(read)

    for key, reads_for_key in grouped.items():
        tag = tag_by_key.get(key)
        max_ts = max(r.timestamp for r in reads_for_key)
        if tag is None or tag.status not in _OWNING_STATUSES:
            result.known_false_read_ids.extend(r.id for r in reads_for_key)
            continue

        result.known_true_read_ids.extend(r.id for r in reads_for_key)
        # D2: promote registered → active on first matching read,
        # populate first_seen_at if NULL, bump last_seen_at when the
        # observed batch is newer than the stored value.
        new_status: str | None = None
        if tag.status == "registered":
            new_status = "active"
            result.promoted_count += 1
        new_first: datetime | None = None
        if tag.first_seen_at is None:
            new_first = max_ts
        new_last = max_ts
        if tag.last_seen_at is not None and tag.last_seen_at >= max_ts:
            # Stored value is already newer — common when a higher-id
            # batch raced ahead of us. Skip the bump rather than
            # rewinding the clock. But we still need to write *some*
            # last_seen_at here for the consistency invariant: if the
            # row is being updated for first_seen_at or status, keep
            # the stored last_seen_at.
            new_last = tag.last_seen_at
        if new_status is None and new_first is None and new_last == tag.last_seen_at:
            # Nothing actually changes — skip the no-op update.
            continue
        result.tag_updates.append(
            _TagUpdate(
                tag_id=tag.id,
                new_status=new_status,
                new_first_seen_at=new_first,
                new_last_seen_at=new_last,
            )
        )
    return result


class TagRegistrarWorker:
    """Sprint 50 Phase D worker.

    Runs alongside the inventory + dwell + alert-delivery workers in
    the ``workers_inline`` lifespan slot (or in the dedicated worker
    container). Holds no in-process state between runs — the
    ``tag_reads.tag_known IS NULL`` predicate is the entire queue.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        interval_s: float = 1.0,
        batch_size: int = 500,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        if interval_s <= 0:
            raise ValueError("interval_s must be > 0")
        self._session_factory = session_factory
        self._interval_s = interval_s
        self._batch_size = batch_size
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "TagRegistrarWorker started (interval=%.2fs, batch=%d)",
            self._interval_s,
            self._batch_size,
        )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("TagRegistrarWorker stopped")

    async def _loop(self) -> None:
        while True:
            try:
                processed = await self.run_once()
            except Exception:  # pragma: no cover - defensive
                logger.exception("TagRegistrarWorker pass failed")
                processed = 0
            # Sleep only when the queue is drained (partial batch).
            # A full batch implies more work waiting — loop immediately
            # so we converge under burst.
            if processed < self._batch_size:
                await asyncio.sleep(self._interval_s)

    async def run_once(self) -> int:
        """One drain pass — returns the number of reads processed.

        Public for tests and operator triggers (e.g. an admin-only
        ``POST /admin/tag-registrar/drain`` if we ever need a manual
        kick; not built in D1).
        """
        async with self._session_factory() as session:
            reads = await self._fetch_unprocessed(session)
            if not reads:
                return 0

            tags = await self._fetch_matching_tags(session, reads)
            classification = classify_reads(reads, tags)

            await self._apply_known_writes(session, classification)
            await self._apply_tag_mutations(session, tags, classification)
            await session.commit()

            self._record_metrics(reads, classification)
            return len(reads)

    # ---- I/O helpers ----

    async def _fetch_unprocessed(self, session: AsyncSession) -> list[_ReadRow]:
        stmt = (
            select(
                TagReadModel.id,
                TagReadModel.tenant_id,
                TagReadModel.epc_hex,
                TagReadModel.timestamp,
            )
            .where(TagReadModel.tag_known.is_(None))
            .limit(self._batch_size)
        )
        rows = (await session.execute(stmt)).all()
        return [
            _ReadRow(
                id=row.id,
                tenant_id=row.tenant_id,
                epc_hex=row.epc_hex,
                timestamp=row.timestamp,
            )
            for row in rows
        ]

    async def _fetch_matching_tags(
        self, session: AsyncSession, reads: Sequence[_ReadRow]
    ) -> list[TagModel]:
        per_tenant: dict[uuid.UUID, set[str]] = defaultdict(set)
        for r in reads:
            if r.epc_hex is None:
                continue
            per_tenant[r.tenant_id].add(normalize_epc_hex(r.epc_hex))
        if not per_tenant:
            return []
        conds = [
            and_(TagModel.tenant_id == tid, TagModel.epc_hex.in_(epcs))
            for tid, epcs in per_tenant.items()
        ]
        stmt = select(TagModel).where(or_(*conds))
        return list((await session.execute(stmt)).scalars().all())

    async def _apply_known_writes(self, session: AsyncSession, c: _Classification) -> None:
        if c.known_true_read_ids:
            await session.execute(
                update(TagReadModel)
                .where(TagReadModel.id.in_(c.known_true_read_ids))
                .values(tag_known=True)
            )
        if c.known_false_read_ids:
            await session.execute(
                update(TagReadModel)
                .where(TagReadModel.id.in_(c.known_false_read_ids))
                .values(tag_known=False)
            )

    async def _apply_tag_mutations(
        self,
        session: AsyncSession,
        tags: Sequence[TagModel],
        c: _Classification,
    ) -> None:
        if not c.tag_updates:
            return
        by_id = {t.id: t for t in tags}
        for upd in c.tag_updates:
            tag = by_id.get(upd.tag_id)
            if tag is None:  # pragma: no cover - defensive
                continue
            if upd.new_status is not None:
                tag.status = upd.new_status
            if upd.new_first_seen_at is not None:
                tag.first_seen_at = upd.new_first_seen_at
            # last_seen_at is always assigned (classifier guards no-ops).
            tag.last_seen_at = upd.new_last_seen_at
        # Note: no explicit UPDATE — SQLAlchemy unit-of-work flushes the
        # attribute changes on commit, batched into a single statement
        # per tag. For batch_size=500 + typical registry sizes this is
        # the cheaper path than an explicit UPDATE ... FROM (VALUES ...).
        _ = session  # the in-place mutations above are scoped to the session

    def _record_metrics(self, reads: Sequence[_ReadRow], c: _Classification) -> None:
        # Per-tenant processed counts (high cardinality is acceptable —
        # tenant_id is already on every other counter in the codebase).
        per_tenant_processed: dict[uuid.UUID, int] = defaultdict(int)
        for r in reads:
            per_tenant_processed[r.tenant_id] += 1
        for tenant_id, n in per_tenant_processed.items():
            tag_registrar_processed_counter.add(n, {"tenant_id": str(tenant_id)})
        if c.promoted_count:
            tag_registrar_promoted_counter.add(c.promoted_count)
