"""Unit tests for the UsageMeter."""

from uuid import uuid4

from tagpulse.core.usage_meter import UsageMeter


class TestUsageMeter:
    def test_record_increments_buffer(self) -> None:
        meter = UsageMeter(session_factory=None)  # type: ignore[arg-type]
        tid = uuid4()
        meter.record(tid, "ingestion", "events", 5)
        meter.record(tid, "ingestion", "events", 3)
        assert meter._buffer[(tid, "ingestion", "events")] == 8

    def test_record_multiple_dimensions(self) -> None:
        meter = UsageMeter(session_factory=None)  # type: ignore[arg-type]
        tid = uuid4()
        meter.record(tid, "ingestion", "events", 10)
        meter.record(tid, "api_read", "requests", 5)
        assert meter._buffer[(tid, "ingestion", "events")] == 10
        assert meter._buffer[(tid, "api_read", "requests")] == 5

    def test_record_multiple_tenants(self) -> None:
        meter = UsageMeter(session_factory=None)  # type: ignore[arg-type]
        t1, t2 = uuid4(), uuid4()
        meter.record(t1, "ingestion", "events", 10)
        meter.record(t2, "ingestion", "events", 20)
        assert meter._buffer[(t1, "ingestion", "events")] == 10
        assert meter._buffer[(t2, "ingestion", "events")] == 20
