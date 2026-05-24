"""Unit tests for the Sprint 50 C2 bulk-confirmation-token store.

Covers the single-use TTL-based token primitive that backs the
ADR 028 §Governance #2 dry-run-first contract. Route-level
integration coverage layers on at the DB-backed integration suite
in a later sprint.
"""

from __future__ import annotations

import uuid

import pytest

from tagpulse.core.bulk_confirmation_tokens import (
    BULK_CONFIRMATION_TOKENS,
    BulkConfirmationTokenStore,
    ConfirmationOutcome,
)


@pytest.fixture
def store() -> BulkConfirmationTokenStore:
    return BulkConfirmationTokenStore()


@pytest.fixture
def tenant_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def user_id() -> uuid.UUID:
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestMintAndConsume:
    def test_mint_returns_token_and_ttl(
        self, store: BulkConfirmationTokenStore, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        token, expires_in = store.mint(
            tenant_id=tenant_id,
            user_id=user_id,
            operation="tags.import",
            content_hash="abc123",
            ttl_seconds=600,
        )
        assert isinstance(token, str)
        assert len(token) >= 32  # secrets.token_urlsafe(32) is ~43 chars
        assert expires_in == 600

    def test_consume_with_matching_args_succeeds(
        self, store: BulkConfirmationTokenStore, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        token, _ = store.mint(
            tenant_id=tenant_id,
            user_id=user_id,
            operation="tags.import",
            content_hash="abc123",
        )
        outcome = store.consume(
            token,
            tenant_id=tenant_id,
            user_id=user_id,
            operation="tags.import",
            content_hash="abc123",
        )
        assert outcome is ConfirmationOutcome.OK

    def test_consume_is_single_use(
        self, store: BulkConfirmationTokenStore, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        token, _ = store.mint(
            tenant_id=tenant_id,
            user_id=user_id,
            operation="tags.import",
            content_hash="abc",
        )
        first = store.consume(
            token,
            tenant_id=tenant_id,
            user_id=user_id,
            operation="tags.import",
            content_hash="abc",
        )
        second = store.consume(
            token,
            tenant_id=tenant_id,
            user_id=user_id,
            operation="tags.import",
            content_hash="abc",
        )
        assert first is ConfirmationOutcome.OK
        assert second is ConfirmationOutcome.NOT_FOUND

    def test_user_id_none_is_accepted(
        self, store: BulkConfirmationTokenStore, tenant_id: uuid.UUID
    ) -> None:
        """Tenant-API-key actors have no user_id; the token store must
        still bind them so two key-holders can't ride each other's tokens.
        """
        token, _ = store.mint(
            tenant_id=tenant_id,
            user_id=None,
            operation="tags.import",
            content_hash="abc",
        )
        outcome = store.consume(
            token,
            tenant_id=tenant_id,
            user_id=None,
            operation="tags.import",
            content_hash="abc",
        )
        assert outcome is ConfirmationOutcome.OK


# ---------------------------------------------------------------------------
# Mismatch surface — each guard returns a distinct outcome
# ---------------------------------------------------------------------------


class TestConsumeMismatch:
    def test_unknown_token_returns_not_found(
        self, store: BulkConfirmationTokenStore, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        outcome = store.consume(
            "no-such-token",
            tenant_id=tenant_id,
            user_id=user_id,
            operation="tags.import",
            content_hash="abc",
        )
        assert outcome is ConfirmationOutcome.NOT_FOUND

    def test_wrong_tenant_returns_tenant_mismatch(
        self, store: BulkConfirmationTokenStore, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        token, _ = store.mint(
            tenant_id=tenant_id,
            user_id=user_id,
            operation="tags.import",
            content_hash="abc",
        )
        outcome = store.consume(
            token,
            tenant_id=uuid.uuid4(),
            user_id=user_id,
            operation="tags.import",
            content_hash="abc",
        )
        assert outcome is ConfirmationOutcome.TENANT_MISMATCH

    def test_wrong_user_returns_user_mismatch(
        self, store: BulkConfirmationTokenStore, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        token, _ = store.mint(
            tenant_id=tenant_id,
            user_id=user_id,
            operation="tags.import",
            content_hash="abc",
        )
        outcome = store.consume(
            token,
            tenant_id=tenant_id,
            user_id=uuid.uuid4(),
            operation="tags.import",
            content_hash="abc",
        )
        assert outcome is ConfirmationOutcome.USER_MISMATCH

    def test_wrong_operation_returns_operation_mismatch(
        self, store: BulkConfirmationTokenStore, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        token, _ = store.mint(
            tenant_id=tenant_id,
            user_id=user_id,
            operation="tags.import",
            content_hash="abc",
        )
        outcome = store.consume(
            token,
            tenant_id=tenant_id,
            user_id=user_id,
            operation="tags.patch",
            content_hash="abc",
        )
        assert outcome is ConfirmationOutcome.OPERATION_MISMATCH

    def test_wrong_content_returns_content_mismatch(
        self, store: BulkConfirmationTokenStore, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        """The core ADR 028 guarantee: a different CSV cannot be confirmed."""
        token, _ = store.mint(
            tenant_id=tenant_id,
            user_id=user_id,
            operation="tags.import",
            content_hash="hash-of-csv-A",
        )
        outcome = store.consume(
            token,
            tenant_id=tenant_id,
            user_id=user_id,
            operation="tags.import",
            content_hash="hash-of-csv-B",
        )
        assert outcome is ConfirmationOutcome.CONTENT_MISMATCH

    def test_mismatch_does_not_burn_token(
        self, store: BulkConfirmationTokenStore, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        """A typo on the confirm path must not invalidate the operator's preview."""
        token, _ = store.mint(
            tenant_id=tenant_id,
            user_id=user_id,
            operation="tags.import",
            content_hash="abc",
        )
        bad = store.consume(
            token,
            tenant_id=tenant_id,
            user_id=user_id,
            operation="tags.import",
            content_hash="oops",
        )
        good = store.consume(
            token,
            tenant_id=tenant_id,
            user_id=user_id,
            operation="tags.import",
            content_hash="abc",
        )
        assert bad is ConfirmationOutcome.CONTENT_MISMATCH
        assert good is ConfirmationOutcome.OK


# ---------------------------------------------------------------------------
# TTL behaviour
# ---------------------------------------------------------------------------


class TestExpiry:
    def test_expired_token_returns_expired(
        self, store: BulkConfirmationTokenStore, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        token, _ = store.mint(
            tenant_id=tenant_id,
            user_id=user_id,
            operation="tags.import",
            content_hash="abc",
            ttl_seconds=60,
            now=1000.0,
        )
        outcome = store.consume(
            token,
            tenant_id=tenant_id,
            user_id=user_id,
            operation="tags.import",
            content_hash="abc",
            now=1100.0,  # 100 s later, past the 60 s TTL
        )
        assert outcome is ConfirmationOutcome.EXPIRED

    def test_expired_token_is_evicted(
        self, store: BulkConfirmationTokenStore, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        """Second consume of the same expired token returns NOT_FOUND
        because lazy-eviction dropped it on the first attempt."""
        token, _ = store.mint(
            tenant_id=tenant_id,
            user_id=user_id,
            operation="tags.import",
            content_hash="abc",
            ttl_seconds=60,
            now=1000.0,
        )
        store.consume(
            token,
            tenant_id=tenant_id,
            user_id=user_id,
            operation="tags.import",
            content_hash="abc",
            now=1100.0,
        )
        again = store.consume(
            token,
            tenant_id=tenant_id,
            user_id=user_id,
            operation="tags.import",
            content_hash="abc",
            now=1101.0,
        )
        assert again is ConfirmationOutcome.NOT_FOUND

    def test_token_within_ttl_still_works(
        self, store: BulkConfirmationTokenStore, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        token, _ = store.mint(
            tenant_id=tenant_id,
            user_id=user_id,
            operation="tags.import",
            content_hash="abc",
            ttl_seconds=60,
            now=1000.0,
        )
        outcome = store.consume(
            token,
            tenant_id=tenant_id,
            user_id=user_id,
            operation="tags.import",
            content_hash="abc",
            now=1059.0,  # 1 second inside the window
        )
        assert outcome is ConfirmationOutcome.OK

    def test_mint_with_non_positive_ttl_raises(
        self, store: BulkConfirmationTokenStore, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        with pytest.raises(ValueError):
            store.mint(
                tenant_id=tenant_id,
                user_id=user_id,
                operation="tags.import",
                content_hash="abc",
                ttl_seconds=0,
            )


# ---------------------------------------------------------------------------
# Module singleton sanity
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_module_singleton_is_the_store(self) -> None:
        assert isinstance(BULK_CONFIRMATION_TOKENS, BulkConfirmationTokenStore)

    def test_reset_clears_all_tokens(self) -> None:
        store = BulkConfirmationTokenStore()
        tid = uuid.uuid4()
        uid = uuid.uuid4()
        token, _ = store.mint(
            tenant_id=tid,
            user_id=uid,
            operation="tags.import",
            content_hash="abc",
        )
        store.reset()
        outcome = store.consume(
            token,
            tenant_id=tid,
            user_id=uid,
            operation="tags.import",
            content_hash="abc",
        )
        assert outcome is ConfirmationOutcome.NOT_FOUND
