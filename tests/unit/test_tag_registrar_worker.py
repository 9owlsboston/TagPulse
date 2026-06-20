"""Unit tests for :mod:`tagpulse.workers.tag_registrar_worker` (Sprint 50
Phase D).

Tests focus on the pure :func:`classify_reads` classifier — the
``run_once`` / ``_loop`` plumbing is integration-tested against the
real DB in :mod:`tests.integration.test_tag_registrar_worker` (not
required for D1 unit coverage). The classifier is where all the
ADR-028 §"Gating" semantics live, so it carries the unit budget.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tagpulse.models.database import TagModel
from tagpulse.workers.tag_registrar_worker import (
    TagRegistrarWorker,
    _ReadRow,
    classify_reads,
)


def _tag(
    tenant_id: uuid.UUID,
    epc_hex: str,
    status: str = "registered",
    *,
    first_seen_at: datetime | None = None,
    last_seen_at: datetime | None = None,
) -> TagModel:
    return TagModel(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        epc_hex=epc_hex,
        status=status,
        first_seen_at=first_seen_at,
        last_seen_at=last_seen_at,
    )


def _read(
    tenant_id: uuid.UUID,
    epc_hex: str | None,
    timestamp: datetime | None = None,
) -> _ReadRow:
    return _ReadRow(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        epc_hex=epc_hex,
        timestamp=timestamp or datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
    )


# ----- Constructor guards -----


def test_constructor_rejects_zero_batch_size() -> None:
    with pytest.raises(ValueError, match="batch_size"):
        TagRegistrarWorker(session_factory=object(), batch_size=0)  # type: ignore[arg-type]


def test_constructor_rejects_nonpositive_interval() -> None:
    with pytest.raises(ValueError, match="interval_s"):
        TagRegistrarWorker(session_factory=object(), interval_s=0)  # type: ignore[arg-type]


# ----- Empty inputs -----


def test_classify_empty_returns_empty() -> None:
    result = classify_reads([], [])
    assert result.known_true_read_ids == []
    assert result.known_false_read_ids == []
    assert result.tag_updates == []
    assert result.promoted_count == 0


# ----- D1: tag_known classification -----


def test_active_tag_yields_known_true() -> None:
    t_id = uuid.uuid4()
    tag = _tag(t_id, "AABB", status="active", last_seen_at=datetime(2026, 1, 1, tzinfo=UTC))
    read = _read(t_id, "aabb")  # lowercase → normalize to AABB
    result = classify_reads([read], [tag])
    assert result.known_true_read_ids == [read.id]
    assert result.known_false_read_ids == []


def test_registered_tag_yields_known_true_and_promotes() -> None:
    t_id = uuid.uuid4()
    tag = _tag(t_id, "DEADBEEF", status="registered")
    read = _read(t_id, "deadbeef")
    result = classify_reads([read], [tag])
    assert result.known_true_read_ids == [read.id]
    assert result.promoted_count == 1
    assert len(result.tag_updates) == 1
    upd = result.tag_updates[0]
    assert upd.tag_id == tag.id
    assert upd.new_status == "active"
    assert upd.new_first_seen_at == read.timestamp
    assert upd.new_last_seen_at == read.timestamp


@pytest.mark.parametrize("terminal_status", ["retired", "defective", "transferred_out"])
def test_terminal_status_yields_known_false(terminal_status: str) -> None:
    t_id = uuid.uuid4()
    tag = _tag(t_id, "AABB", status=terminal_status)
    read = _read(t_id, "aabb")
    result = classify_reads([read], [tag])
    assert result.known_false_read_ids == [read.id]
    assert result.known_true_read_ids == []
    assert result.tag_updates == []  # no D2 mutations on terminal tags
    assert result.promoted_count == 0


def test_missing_tag_yields_known_false() -> None:
    t_id = uuid.uuid4()
    read = _read(t_id, "AABB")
    # No tags at all — EPC unknown.
    result = classify_reads([read], [])
    assert result.known_false_read_ids == [read.id]
    assert result.known_true_read_ids == []


def test_null_epc_yields_known_false_without_tag_lookup() -> None:
    t_id = uuid.uuid4()
    read = _read(t_id, None)
    # Even if there were tags, the EPC-less read can never be "known".
    tag = _tag(t_id, "AABB", status="active")
    result = classify_reads([read], [tag])
    assert result.known_false_read_ids == [read.id]
    assert result.tag_updates == []


def test_cross_tenant_isolation() -> None:
    t1, t2 = uuid.uuid4(), uuid.uuid4()
    tag_in_t1 = _tag(t1, "AABB", status="active")
    # Tenant 2 has a read for the same EPC but no tag — must be FALSE.
    read_t2 = _read(t2, "aabb")
    result = classify_reads([read_t2], [tag_in_t1])
    assert result.known_false_read_ids == [read_t2.id]
    assert result.known_true_read_ids == []


# ----- D2: first_seen_at / last_seen_at / promotion semantics -----


def test_first_seen_at_only_set_when_null() -> None:
    t_id = uuid.uuid4()
    earlier = datetime(2025, 1, 1, tzinfo=UTC)
    tag = _tag(t_id, "AABB", status="active", first_seen_at=earlier, last_seen_at=earlier)
    later = datetime(2026, 5, 18, tzinfo=UTC)
    read = _read(t_id, "aabb", timestamp=later)
    result = classify_reads([read], [tag])
    assert len(result.tag_updates) == 1
    upd = result.tag_updates[0]
    assert upd.new_first_seen_at is None  # preserved
    assert upd.new_last_seen_at == later
    assert upd.new_status is None  # already active


def test_last_seen_at_not_rewound_when_stored_is_newer() -> None:
    t_id = uuid.uuid4()
    newer = datetime(2026, 5, 18, tzinfo=UTC)
    older = newer - timedelta(hours=1)
    tag = _tag(t_id, "AABB", status="active", first_seen_at=newer, last_seen_at=newer)
    read = _read(t_id, "aabb", timestamp=older)
    result = classify_reads([read], [tag])
    # The read is classified TRUE but no tag update is emitted because
    # the stored last_seen_at is already newer and nothing else changes.
    assert result.known_true_read_ids == [read.id]
    assert result.tag_updates == []
    assert result.promoted_count == 0


def test_batch_picks_max_timestamp_per_tag() -> None:
    t_id = uuid.uuid4()
    tag = _tag(t_id, "AABB", status="registered")
    t1 = datetime(2026, 5, 18, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    t3 = datetime(2026, 5, 18, 11, 0, tzinfo=UTC)
    reads = [
        _read(t_id, "aabb", timestamp=t1),
        _read(t_id, "AABB", timestamp=t2),
        _read(t_id, "aabb", timestamp=t3),
    ]
    result = classify_reads(reads, [tag])
    assert sorted(result.known_true_read_ids) == sorted(r.id for r in reads)
    assert len(result.tag_updates) == 1
    upd = result.tag_updates[0]
    assert upd.new_status == "active"
    assert upd.new_first_seen_at == t2
    assert upd.new_last_seen_at == t2
    assert result.promoted_count == 1


def test_mixed_batch_partitions_correctly() -> None:
    t_id = uuid.uuid4()
    active_tag = _tag(
        t_id,
        "AAAA",
        status="active",
        first_seen_at=datetime(2026, 1, 1, tzinfo=UTC),
        last_seen_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    retired_tag = _tag(t_id, "BBBB", status="retired")
    registered_tag = _tag(t_id, "CCCC", status="registered")

    r_active = _read(t_id, "aaaa", timestamp=datetime(2026, 5, 18, tzinfo=UTC))
    r_retired = _read(t_id, "bbbb")
    r_registered = _read(t_id, "cccc", timestamp=datetime(2026, 5, 18, tzinfo=UTC))
    r_unknown = _read(t_id, "ffff")
    r_null = _read(t_id, None)

    result = classify_reads(
        [r_active, r_retired, r_registered, r_unknown, r_null],
        [active_tag, retired_tag, registered_tag],
    )
    assert sorted(result.known_true_read_ids) == sorted([r_active.id, r_registered.id])
    assert sorted(result.known_false_read_ids) == sorted([r_retired.id, r_unknown.id, r_null.id])
    # Two tag updates: active (last_seen_at bump) + registered (promotion).
    assert len(result.tag_updates) == 2
    assert result.promoted_count == 1
