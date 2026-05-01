"""SQLite-backed offline buffer.

Why SQLite instead of an in-memory deque?

- **Survives restarts.** Pis crash. SD cards reboot. Data accepted from the
  hardware loop must not be lost just because we lost MQTT and got SIGTERM.
- **Bounded.** ``max_rows`` and ``max_age_s`` are enforced on every put;
  oldest rows are evicted (ring buffer semantics).
- **Cheap.** SQLite WAL mode handles 1 writer + 1 reader from the same
  process happily; no external service needed.

The buffer stores already-serialized events (kind + topic + JSON payload) so
the publisher just pulls and writes to MQTT.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterable
from pathlib import Path

from tagpulse_edge.events import EventKind, OutboundEvent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,
    topic        TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    enqueued_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_outbox_enqueued ON outbox(enqueued_at);
"""


class OutboxFullError(RuntimeError):
    pass


class Outbox:
    """Thread-safe persistent queue. Single-process, single-DB."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_rows: int,
        max_age_s: float,
    ) -> None:
        if max_rows <= 0:
            raise ValueError("max_rows must be > 0")
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._max_rows = max_rows
        self._max_age_s = max_age_s
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self._path), check_same_thread=False, isolation_level=None
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)

    # -- Lifecycle --

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- Mutation --

    def put(self, event: OutboundEvent) -> None:
        """Persist one event. Evicts oldest rows if over capacity."""
        payload_json = json.dumps(event.payload, separators=(",", ":"))
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO outbox(kind, topic, payload_json, enqueued_at) "
                "VALUES (?, ?, ?, ?)",
                (event.kind, event.topic, payload_json, event.enqueued_at),
            )
            event.rowid = int(cur.lastrowid or 0)
            self._evict_locked()

    def ack(self, rowids: Iterable[int]) -> int:
        """Delete acknowledged rows. Returns the number deleted."""
        ids = list(rowids)
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        with self._lock:
            cur = self._conn.execute(
                f"DELETE FROM outbox WHERE id IN ({placeholders})", ids
            )
            return int(cur.rowcount or 0)

    def _evict_locked(self) -> None:
        # Time-based eviction first.
        if self._max_age_s > 0:
            cutoff = time.monotonic() - self._max_age_s
            self._conn.execute("DELETE FROM outbox WHERE enqueued_at < ?", (cutoff,))
        # Size-based eviction (drop oldest).
        (count,) = self._conn.execute("SELECT COUNT(*) FROM outbox").fetchone()
        if count > self._max_rows:
            overflow = count - self._max_rows
            self._conn.execute(
                "DELETE FROM outbox WHERE id IN ("
                "  SELECT id FROM outbox ORDER BY id ASC LIMIT ?"
                ")",
                (overflow,),
            )

    # -- Reads --

    def peek(self, limit: int) -> list[OutboundEvent]:
        """Return up to ``limit`` oldest rows without removing them."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, kind, topic, payload_json, enqueued_at "
                "FROM outbox ORDER BY id ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            OutboundEvent(
                kind=_validate_kind(kind),
                topic=topic,
                payload=json.loads(payload_json),
                enqueued_at=enqueued_at,
                rowid=rowid,
            )
            for (rowid, kind, topic, payload_json, enqueued_at) in rows
        ]

    def depth(self) -> int:
        with self._lock:
            (count,) = self._conn.execute("SELECT COUNT(*) FROM outbox").fetchone()
            return int(count)


def _validate_kind(value: str) -> EventKind:
    if value not in {"tag-reads", "telemetry", "location", "status", "events"}:
        # Forward-compat: unknown kinds shouldn't crash a drain.
        return "events"  # type: ignore[return-value]
    return value  # type: ignore[return-value]
