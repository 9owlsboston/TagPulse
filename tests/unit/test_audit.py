"""Unit tests for AuditLogger."""


from tagpulse.core.audit import AuditLogger


class TestAuditLogger:
    def test_instantiation(self) -> None:
        # AuditLogger should accept a session
        logger = AuditLogger(session=None)  # type: ignore[arg-type]
        assert logger is not None
