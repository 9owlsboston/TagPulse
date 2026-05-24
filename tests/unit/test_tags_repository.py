"""Pure-Python unit tests for the tag-registry repository layer.

DB-touching paths are covered by the integration suite. This file
focuses on the helpers + exception hierarchy that we can validate
without a live Postgres.
"""

from __future__ import annotations

import uuid

from tagpulse.repositories.timescaledb.tags import (
    StatusTransitionError,
    TagEpcConflictError,
    TagInUseError,
)


class TestExceptionHierarchy:
    def test_conflict_is_value_error(self) -> None:
        exc = TagEpcConflictError("dup")
        assert isinstance(exc, ValueError)

    def test_in_use_is_runtime_error(self) -> None:
        tag_id = uuid.uuid4()
        exc = TagInUseError(tag_id, 3)
        assert isinstance(exc, RuntimeError)
        assert exc.tag_id == tag_id
        assert exc.binding_count == 3
        assert "3" in str(exc)

    def test_status_transition_is_value_error(self) -> None:
        # Re-exported from services.tags — assert the alias works.
        exc = StatusTransitionError("nope")
        assert isinstance(exc, ValueError)
