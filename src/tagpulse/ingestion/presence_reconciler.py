"""Synchronous EPC presence reconciler for v2 wire-format messages.

Sprint 46, ADR-026 §4.2. Owns the ``tag_presence`` current-state table:
applies snap (``t=0``) / appeared (``t=1``) / disappeared (``t=2``)
messages to the table inside the caller's transaction and emits
``Topic.SIGNALING_TAG_APPEARED`` / ``Topic.SIGNALING_TAG_DISAPPEARED``
events for each absent → present / present → absent transition.

Design notes (ADR-026):

- **No buffering, no window.** Reconciliation is synchronous on receipt
  of every snap. Lost ``t=2`` messages self-heal at the next snap.
- **No ``last_seq``, no ``suspect`` column.** The per-cycle counter was
  removed from the v2 wire (spec §3.1); buffered-snap state would have
  nothing to be suspicious about.
- **Tenant isolation:** the caller MUST set ``app.current_tenant_id``
  on the session before invoking any function here. The
  ``WHERE tenant_id = ?`` predicates are belt-and-braces; RLS is the
  trust root.
- **Identity:** the reconciler trusts the ``(tenant_id, device_id)``
  pair passed in by the subscriber (already derived from the topic).
  The spec's §4.5 SN→device_id lookup + JWT cross-check happen earlier
  in the dispatch chain, not here.

Event payload schema (both topics)::

    {
        "tenant_id": str(UUID),
        "device_id": str(UUID),
        "epc": str,                # uppercase hex
        "observed_at": str (ISO 8601 UTC),
        "source": "delta" | "snap",
    }
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tagpulse.events.protocol import Event, EventBus, Topic
from tagpulse.models.database import TagPresenceModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tagpulse.ingestion.wm_wire_format import (
        WmAppearedMessage,
        WmDisappearedMessage,
        WmSnapMessage,
    )

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts_to_datetime(ts_ms: int) -> datetime:
    """Convert a v2 wire ``ts`` (epoch milliseconds, UTC) to a tz-aware ``datetime``."""
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC)


async def _emit(
    event_bus: EventBus,
    topic: Topic,
    *,
    tenant_id: UUID,
    device_id: UUID,
    epc: str,
    observed_at: datetime,
    source: str,
) -> None:
    """Publish one presence-transition event."""
    await event_bus.publish(
        topic,
        Event(
            id=uuid4(),
            topic=topic,
            timestamp=observed_at,
            payload={
                "tenant_id": str(tenant_id),
                "device_id": str(device_id),
                "epc": epc,
                "observed_at": observed_at.isoformat(),
                "source": source,
            },
        ),
    )


def _collapse_snap_entries(
    msg: WmSnapMessage,
) -> dict[str, tuple[int, int]]:
    """Collapse multi-antenna entries to one per EPC, keeping the strongest RSSI.

    Spec §4.2: "Multi-antenna entries for the same EPC inside one
    ``epcs[]`` collapse to one ``present`` row via the upsert's
    ``ON CONFLICT`` clause (highest ``rssi`` wins for ``last_rssi`` /
    ``last_antenna``)." RSSI is dBm (negative); a higher value (closer
    to 0) is stronger. Pre-collapsing client-side avoids a per-row
    ``GREATEST`` in the upsert and lets us emit a single row per EPC.

    Returns ``{epc: (rssi, antenna)}``.
    """
    out: dict[str, tuple[int, int]] = {}
    for entry in msg.epcs:
        existing = out.get(entry.epc)
        if existing is None or entry.rssi > existing[0]:
            out[entry.epc] = (entry.rssi, entry.an)
    return out


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


async def reconcile_snap(
    session: AsyncSession,
    event_bus: EventBus,
    *,
    tenant_id: UUID,
    device_id: UUID,
    msg: WmSnapMessage,
) -> tuple[list[str], list[str]]:
    """Apply a ``t=0`` snapshot per spec §4.2.

    Returns ``(appeared_epcs, disappeared_epcs)`` for the caller's
    logging / metrics. Events are emitted synchronously before return.
    """
    observed_at = _ts_to_datetime(msg.ts)
    snap_state = _collapse_snap_entries(msg)
    snap_epcs = set(snap_state.keys())

    # 1. Currently-present EPCs for this (tenant, device).
    present_rows = await session.execute(
        select(TagPresenceModel.epc).where(
            TagPresenceModel.tenant_id == tenant_id,
            TagPresenceModel.device_id == device_id,
            TagPresenceModel.status == "present",
        )
    )
    present_epcs = {row[0] for row in present_rows.all()}

    # 2. Upsert every snap entry as present. ``first_seen`` is left
    #    untouched on conflict (only set on insert).
    if snap_state:
        rows = [
            {
                "tenant_id": tenant_id,
                "device_id": device_id,
                "epc": epc,
                "first_seen": observed_at,
                "last_seen": observed_at,
                "status": "present",
                "last_rssi": rssi,
                "last_antenna": antenna,
            }
            for epc, (rssi, antenna) in snap_state.items()
        ]
        stmt = pg_insert(TagPresenceModel).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["tenant_id", "device_id", "epc"],
            set_={
                "last_seen": stmt.excluded.last_seen,
                "status": "present",
                "last_rssi": stmt.excluded.last_rssi,
                "last_antenna": stmt.excluded.last_antenna,
            },
        )
        await session.execute(stmt)

    # 3. Mark previously-present-but-absent EPCs as gone.
    gone_epcs = sorted(present_epcs - snap_epcs)
    if gone_epcs:
        await session.execute(
            update(TagPresenceModel)
            .where(
                TagPresenceModel.tenant_id == tenant_id,
                TagPresenceModel.device_id == device_id,
                TagPresenceModel.epc.in_(gone_epcs),
                TagPresenceModel.status == "present",
            )
            .values(status="gone", last_seen=observed_at)
        )

    # 4. Emit transition events.
    appeared_epcs = sorted(snap_epcs - present_epcs)
    for epc in appeared_epcs:
        await _emit(
            event_bus,
            Topic.SIGNALING_TAG_APPEARED,
            tenant_id=tenant_id,
            device_id=device_id,
            epc=epc,
            observed_at=observed_at,
            source="snap",
        )
    for epc in gone_epcs:
        await _emit(
            event_bus,
            Topic.SIGNALING_TAG_DISAPPEARED,
            tenant_id=tenant_id,
            device_id=device_id,
            epc=epc,
            observed_at=observed_at,
            source="snap",
        )

    return appeared_epcs, gone_epcs


async def apply_appeared(
    session: AsyncSession,
    event_bus: EventBus,
    *,
    tenant_id: UUID,
    device_id: UUID,
    msg: WmAppearedMessage,
) -> bool:
    """Apply a ``t=1`` delta per spec §4.3.

    Upserts ``tag_presence`` to ``present`` and emits
    ``SIGNALING_TAG_APPEARED`` iff the row was not previously
    ``present`` (i.e., new insert OR transition from ``gone``).

    Returns ``True`` if an event was emitted.
    """
    observed_at = _ts_to_datetime(msg.ts)

    prior = await session.execute(
        select(TagPresenceModel.status).where(
            TagPresenceModel.tenant_id == tenant_id,
            TagPresenceModel.device_id == device_id,
            TagPresenceModel.epc == msg.epc,
        )
    )
    prior_status = prior.scalar_one_or_none()

    stmt = pg_insert(TagPresenceModel).values(
        tenant_id=tenant_id,
        device_id=device_id,
        epc=msg.epc,
        first_seen=observed_at,
        last_seen=observed_at,
        status="present",
        last_rssi=msg.rssi,
        last_antenna=msg.an,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["tenant_id", "device_id", "epc"],
        set_={
            "last_seen": stmt.excluded.last_seen,
            "status": "present",
            "last_rssi": stmt.excluded.last_rssi,
            "last_antenna": stmt.excluded.last_antenna,
        },
    )
    await session.execute(stmt)

    is_transition = prior_status != "present"
    if is_transition:
        await _emit(
            event_bus,
            Topic.SIGNALING_TAG_APPEARED,
            tenant_id=tenant_id,
            device_id=device_id,
            epc=msg.epc,
            observed_at=observed_at,
            source="delta",
        )
    return is_transition


async def apply_disappeared(
    session: AsyncSession,
    event_bus: EventBus,
    *,
    tenant_id: UUID,
    device_id: UUID,
    msg: WmDisappearedMessage,
) -> bool:
    """Apply a ``t=2`` delta per spec §4.3.

    Updates ``tag_presence`` to ``gone`` iff the EPC was previously
    ``present`` for this ``(tenant, device)`` and emits
    ``SIGNALING_TAG_DISAPPEARED`` for the transition. If the EPC was
    never seen, logs at debug + returns False (per spec §6
    ``tagpulse_mqtt_wm_sub_no_presence_total`` — counter wiring in
    Phase E). If the EPC was already ``gone``, no-op.

    Returns ``True`` if an event was emitted.
    """
    observed_at = _ts_to_datetime(msg.ts)

    prior = await session.execute(
        select(TagPresenceModel.status).where(
            TagPresenceModel.tenant_id == tenant_id,
            TagPresenceModel.device_id == device_id,
            TagPresenceModel.epc == msg.epc,
        )
    )
    prior_status = prior.scalar_one_or_none()

    if prior_status is None:
        # Never-seen EPC: log + (future Phase E) bump
        # tagpulse_mqtt_wm_sub_no_presence_total. Do NOT reject (spec §6).
        logger.debug(
            "t=2 for never-seen epc=%s device=%s tenant=%s — ignored",
            msg.epc,
            device_id,
            tenant_id,
        )
        return False

    if prior_status == "gone":
        # Already gone — refresh last_seen so duplicate-sub replays
        # don't lose the timestamp, but do not re-emit.
        await session.execute(
            update(TagPresenceModel)
            .where(
                TagPresenceModel.tenant_id == tenant_id,
                TagPresenceModel.device_id == device_id,
                TagPresenceModel.epc == msg.epc,
            )
            .values(last_seen=observed_at)
        )
        return False

    # prior_status == "present" → transition.
    await session.execute(
        update(TagPresenceModel)
        .where(
            TagPresenceModel.tenant_id == tenant_id,
            TagPresenceModel.device_id == device_id,
            TagPresenceModel.epc == msg.epc,
        )
        .values(status="gone", last_seen=observed_at)
    )
    await _emit(
        event_bus,
        Topic.SIGNALING_TAG_DISAPPEARED,
        tenant_id=tenant_id,
        device_id=device_id,
        epc=msg.epc,
        observed_at=observed_at,
        source="delta",
    )
    return True


__all__ = [
    "apply_appeared",
    "apply_disappeared",
    "reconcile_snap",
]
