"""Tests for the SQLite-backed Outbox."""

from __future__ import annotations

import time
from pathlib import Path

from tagpulse_edge.buffer import Outbox
from tagpulse_edge.events import OutboundEvent


def _event(seq: int) -> OutboundEvent:
    return OutboundEvent(
        kind="tag-reads",
        topic="tenants/x/devices/y/tag-reads",
        payload={"seq": seq},
        enqueued_at=time.monotonic(),
    )


def test_put_and_peek_returns_in_order(tmp_path: Path) -> None:
    box = Outbox(tmp_path / "x.sqlite", max_rows=100, max_age_s=3600)
    for i in range(5):
        box.put(_event(i))
    rows = box.peek(10)
    assert [r.payload["seq"] for r in rows] == [0, 1, 2, 3, 4]
    assert all(r.rowid is not None for r in rows)


def test_ack_removes_rows(tmp_path: Path) -> None:
    box = Outbox(tmp_path / "x.sqlite", max_rows=100, max_age_s=3600)
    for i in range(3):
        box.put(_event(i))
    rows = box.peek(10)
    deleted = box.ack(r.rowid for r in rows if r.rowid is not None)
    assert deleted == 3
    assert box.depth() == 0


def test_ring_eviction_drops_oldest(tmp_path: Path) -> None:
    box = Outbox(tmp_path / "x.sqlite", max_rows=3, max_age_s=3600)
    for i in range(5):
        box.put(_event(i))
    rows = box.peek(10)
    assert [r.payload["seq"] for r in rows] == [2, 3, 4]


def test_persists_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "x.sqlite"
    box = Outbox(path, max_rows=100, max_age_s=3600)
    box.put(_event(42))
    box.close()

    box2 = Outbox(path, max_rows=100, max_age_s=3600)
    rows = box2.peek(10)
    assert len(rows) == 1
    assert rows[0].payload["seq"] == 42
