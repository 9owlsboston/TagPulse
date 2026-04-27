"""Unit tests for admin operations — dead letter and audit log schemas."""

import uuid
from datetime import UTC, datetime

import pytest

from tagpulse.api.routes.admin_ops import DeadLetterResponse
from tagpulse.core.audit import AuditLogger


class TestDeadLetterResponse:
    """Validate dead letter response schema."""

    def test_valid_dead_letter(self) -> None:
        now = datetime.now(UTC)
        dl = DeadLetterResponse(
            id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            topic="tag_read.created",
            payload={"tag_id": "ABC123"},
            error_message="Handler failed",
            retry_count=0,
            status="pending",
            failed_at=now,
        )
        assert dl.status == "pending"
        assert dl.retry_count == 0

    def test_dead_letter_no_tenant(self) -> None:
        now = datetime.now(UTC)
        dl = DeadLetterResponse(
            id=uuid.uuid4(),
            tenant_id=None,
            topic="tag_read.created",
            payload={},
            error_message="err",
            retry_count=1,
            status="retried",
            failed_at=now,
        )
        assert dl.tenant_id is None


class TestAuditLoggerInstantiation:
    """Verify audit logger accepts user_id parameter."""

    def test_instantiation(self) -> None:
        logger = AuditLogger(session=None)  # type: ignore[arg-type]
        assert logger is not None

    @pytest.mark.asyncio
    async def test_log_signature_accepts_user_id(self) -> None:
        """Verify the log method accepts the user_id keyword argument."""
        import inspect

        sig = inspect.signature(AuditLogger.log)
        assert "user_id" in sig.parameters
