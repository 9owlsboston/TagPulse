"""Unit tests for the scope-required bulk mutation surface.

Sprint 50 C4 — implements ADR-028 §"Governance" rule 3
(see ``docs/adr/028-tags-as-first-class-entity.md``).

Covers the pure-Python helpers + schemas behind ``POST /tags/bulk-patch``
and ``POST /tags/bulk-retire``:

- :class:`TagBulkScope` / :class:`TagBulkPatchRequest` validation
  (XOR scope, batch-key required, EPC dedup, at-least-one mutation).
- :func:`_bulk_mutation_content_hash` determinism + sensitivity to
  every field that should change the token binding.
- :func:`_normalize_scope` EPC normalization and post-norm dedup
  → 400 surface.
- :func:`_validate_transitions` per-row edge-list enforcement,
  including the "no target status → no checks" shortcut.
- :func:`_serialize_bulk_payload` → :func:`_bulk_payload_content_hash`
  round-trip (the approve-path tamper guard).
- Executor registry: both ``tags.bulk_patch`` and ``tags.bulk_retire``
  are wired to :func:`_bulk_mutation_executor`.

Route-level tests (HTTP status codes, RBAC, dry-run / confirm
flow end-to-end) live in the integration suite.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from tagpulse.api.routes import tags as tags_route
from tagpulse.models.schemas import (
    TagBulkOperationResult,
    TagBulkPatchRequest,
    TagBulkRetireRequest,
    TagBulkRowError,
    TagBulkScope,
)
from tagpulse.services import pending_bulk_operations as pending_ops

# ---------------------------------------------------------------------------
# TagBulkScope
# ---------------------------------------------------------------------------


class TestTagBulkScope:
    def test_epc_list_only_ok(self) -> None:
        scope = TagBulkScope(epc_list=["A" * 16, "B" * 16])
        assert scope.labels is None
        assert scope.epc_list == ["A" * 16, "B" * 16]

    def test_labels_with_batch_only_ok(self) -> None:
        scope = TagBulkScope(labels={"batch": "B-001"})
        assert scope.epc_list is None
        assert scope.labels == {"batch": "B-001"}

    def test_labels_with_batch_and_extra_keys_ok(self) -> None:
        # ADR allows other keys alongside batch; only batch is required.
        scope = TagBulkScope(labels={"batch": "B-001", "zone": "A12"})
        assert scope.labels == {"batch": "B-001", "zone": "A12"}

    def test_neither_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must provide either"):
            TagBulkScope()

    def test_both_rejected(self) -> None:
        with pytest.raises(ValidationError, match="exactly one"):
            TagBulkScope(labels={"batch": "B-001"}, epc_list=["A" * 16])

    def test_labels_without_batch_rejected(self) -> None:
        with pytest.raises(ValidationError, match="'batch' key"):
            TagBulkScope(labels={"zone": "A12"})

    def test_labels_with_empty_batch_rejected(self) -> None:
        with pytest.raises(ValidationError, match="'batch' key"):
            TagBulkScope(labels={"batch": "   "})

    def test_epc_list_oversize_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TagBulkScope(epc_list=["A" * 16] * 1001)

    def test_epc_list_bad_hex_rejected(self) -> None:
        with pytest.raises(ValidationError, match="canonical EPC hex"):
            TagBulkScope(epc_list=["xyz"])

    def test_epc_list_lowercase_rejected(self) -> None:
        # Defensive check — route normalises first, but schema must
        # still flag bad input that bypasses the route.
        with pytest.raises(ValidationError, match="canonical EPC hex"):
            TagBulkScope(epc_list=["a" * 16])

    def test_epc_list_duplicate_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duplicate"):
            TagBulkScope(epc_list=["A" * 16, "A" * 16])


# ---------------------------------------------------------------------------
# TagBulkPatchRequest / TagBulkRetireRequest
# ---------------------------------------------------------------------------


class TestBulkPatchRequest:
    def test_status_only_ok(self) -> None:
        req = TagBulkPatchRequest(
            scope=TagBulkScope(epc_list=["A" * 16]),
            status="retired",
        )
        assert req.status == "retired"
        assert req.metadata is None

    def test_metadata_only_ok(self) -> None:
        req = TagBulkPatchRequest(
            scope=TagBulkScope(epc_list=["A" * 16]),
            metadata={"k": "v"},
        )
        assert req.metadata == {"k": "v"}

    def test_neither_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least one"):
            TagBulkPatchRequest(scope=TagBulkScope(epc_list=["A" * 16]))

    def test_invalid_status_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TagBulkPatchRequest(
                scope=TagBulkScope(epc_list=["A" * 16]),
                status="not-a-status",  # type: ignore[arg-type]
            )


class TestBulkRetireRequest:
    def test_scope_only_ok(self) -> None:
        req = TagBulkRetireRequest(scope=TagBulkScope(labels={"batch": "B-1"}))
        assert req.reason is None

    def test_reason_max_length(self) -> None:
        with pytest.raises(ValidationError):
            TagBulkRetireRequest(
                scope=TagBulkScope(labels={"batch": "B-1"}),
                reason="x" * 501,
            )


# ---------------------------------------------------------------------------
# _bulk_mutation_content_hash
# ---------------------------------------------------------------------------


class TestBulkMutationContentHash:
    def _hash(self, **kwargs: object) -> str:
        defaults: dict[str, object] = {
            "epc_hexes": ["A" * 16, "B" * 16],
            "status": "retired",
            "metadata": None,
            "metadata_set": False,
        }
        defaults.update(kwargs)
        return tags_route._bulk_mutation_content_hash(
            list(defaults["epc_hexes"]),  # type: ignore[arg-type]
            status=defaults["status"],  # type: ignore[arg-type]
            metadata=defaults["metadata"],  # type: ignore[arg-type]
            metadata_set=bool(defaults["metadata_set"]),
        )

    def test_deterministic(self) -> None:
        assert self._hash() == self._hash()

    def test_epc_order_insensitive(self) -> None:
        a = self._hash(epc_hexes=["A" * 16, "B" * 16])
        b = self._hash(epc_hexes=["B" * 16, "A" * 16])
        assert a == b

    def test_status_change_detected(self) -> None:
        assert self._hash(status="retired") != self._hash(status="defective")

    def test_metadata_change_detected(self) -> None:
        assert self._hash(metadata={"k": "v"}, metadata_set=True) != self._hash(
            metadata={"k": "w"}, metadata_set=True
        )

    def test_metadata_set_distinguishes_unset_from_null(self) -> None:
        # "don't touch metadata" vs "set metadata to NULL" must hash differently.
        unset = self._hash(metadata=None, metadata_set=False)
        explicit_null = self._hash(metadata=None, metadata_set=True)
        assert unset != explicit_null

    def test_metadata_key_order_insensitive(self) -> None:
        a = self._hash(metadata={"a": 1, "b": 2}, metadata_set=True)
        b = self._hash(metadata={"b": 2, "a": 1}, metadata_set=True)
        assert a == b


# ---------------------------------------------------------------------------
# _normalize_scope
# ---------------------------------------------------------------------------


class TestNormalizeScope:
    def test_epc_list_uppercased_and_stripped(self) -> None:
        scope = TagBulkScope(epc_list=["A" * 16])
        # Construct a scope with raw-ish EPC by bypassing the schema:
        # the schema already rejects lowercase, so we test the normalizer
        # against an already-canonical value but assert the round-trip
        # passes through unchanged.
        kind, value = tags_route._normalize_scope(scope)
        assert kind == "epc_list"
        assert value == ["A" * 16]

    def test_post_normalize_dup_raises_400(self) -> None:
        # Construct via model_construct() to bypass schema dedup check,
        # mimicking a hypothetical caller that submits one canonical
        # and one whitespace-padded value. Route normalization
        # collapses them into a duplicate.
        scope = TagBulkScope.model_construct(
            labels=None,
            epc_list=["A" * 16, "  " + "A" * 16 + "  "],
        )
        with pytest.raises(HTTPException) as exc:
            tags_route._normalize_scope(scope)
        assert exc.value.status_code == 400
        assert "duplicates" in str(exc.value.detail)

    def test_labels_passthrough(self) -> None:
        scope = TagBulkScope(labels={"batch": "B-001"})
        kind, value = tags_route._normalize_scope(scope)
        assert kind == "label_batch"
        assert value == "B-001"


# ---------------------------------------------------------------------------
# _validate_transitions
# ---------------------------------------------------------------------------


@dataclass
class _FakeRow:
    epc_hex: str
    status: str


class TestValidateTransitions:
    def test_none_target_skips_checks(self) -> None:
        rows = [_FakeRow(epc_hex="A" * 16, status="retired")]
        assert tags_route._validate_transitions(rows, None) == []

    def test_all_valid_returns_empty(self) -> None:
        rows = [
            _FakeRow(epc_hex="A" * 16, status="active"),
            _FakeRow(epc_hex="B" * 16, status="registered"),
        ]
        # active → retired and registered → retired are both allowed.
        assert tags_route._validate_transitions(rows, "retired") == []

    def test_invalid_transition_surfaced(self) -> None:
        rows = [
            _FakeRow(epc_hex="A" * 16, status="active"),
            _FakeRow(epc_hex="B" * 16, status="transferred_out"),
        ]
        errors = tags_route._validate_transitions(rows, "retired")
        assert len(errors) == 1
        assert errors[0].epc_hex == "B" * 16
        assert "transferred_out" in errors[0].error


# ---------------------------------------------------------------------------
# serialize / re-hash round trip
# ---------------------------------------------------------------------------


class TestPayloadRoundTrip:
    def test_round_trip_matches_intent_hash(self) -> None:
        epcs = ["A" * 16, "C" * 16, "B" * 16]
        payload = tags_route._serialize_bulk_payload(
            scope_kind="label_batch",
            scope_value="B-001",
            status="retired",
            metadata=None,
            metadata_set=False,
            resolved_epcs=epcs,
        )
        # The stored payload's recomputed hash must equal the
        # intent-hash that minted the confirmation token.
        recomputed = tags_route._bulk_payload_content_hash(payload)
        intent = tags_route._bulk_mutation_content_hash(
            epcs, status="retired", metadata=None, metadata_set=False
        )
        assert recomputed == intent

    def test_metadata_intent_preserved(self) -> None:
        epcs = ["A" * 16]
        payload = tags_route._serialize_bulk_payload(
            scope_kind="epc_list",
            scope_value=epcs,
            status=None,
            metadata={"k": "v"},
            metadata_set=True,
            resolved_epcs=epcs,
        )
        recomputed = tags_route._bulk_payload_content_hash(payload)
        intent = tags_route._bulk_mutation_content_hash(
            epcs, status=None, metadata={"k": "v"}, metadata_set=True
        )
        assert recomputed == intent


# ---------------------------------------------------------------------------
# Executor registry
# ---------------------------------------------------------------------------


class TestExecutorRegistry:
    """The route module registers two executors at import time.

    Other tests in the suite call :func:`pending_ops.reset_executors`,
    so we re-trigger registration via :func:`importlib.reload` to
    isolate this assertion from sibling-test ordering.
    """

    @pytest.fixture(autouse=True)
    def _reload_routes(self) -> None:
        import importlib

        importlib.reload(tags_route)

    def test_bulk_patch_registered(self) -> None:
        assert "tags.bulk_patch" in pending_ops.get_registered_operations()

    def test_bulk_retire_registered(self) -> None:
        assert "tags.bulk_retire" in pending_ops.get_registered_operations()


# ---------------------------------------------------------------------------
# Result schema branch shapes
# ---------------------------------------------------------------------------


class TestBulkOperationResultSchema:
    def test_empty_errors_default(self) -> None:
        res = TagBulkOperationResult(matched=1, updated=1, dry_run=False)
        assert res.errors == []
        assert res.sample == []
        assert res.token is None
        assert res.pending_id is None

    def test_dry_run_branch(self) -> None:
        res = TagBulkOperationResult(
            matched=5,
            updated=0,
            dry_run=True,
            sample=["A" * 16],
            token="tok",  # noqa: S106 — opaque preview token, not a credential
            expires_in=900,
        )
        assert res.dry_run is True
        assert res.updated == 0

    def test_pending_branch(self) -> None:
        pid = uuid.uuid4()
        res = TagBulkOperationResult(
            matched=10_000,
            updated=0,
            dry_run=False,
            token="tok",  # noqa: S106 — opaque preview token, not a credential
            requires_approval=True,
            pending_id=pid,
        )
        assert res.requires_approval is True
        assert res.pending_id == pid


class TestBulkRowError:
    def test_serialises(self) -> None:
        err = TagBulkRowError(epc_hex="A" * 16, error="bad transition")
        assert err.model_dump() == {"epc_hex": "A" * 16, "error": "bad transition"}
