"""Single-use confirmation-token store for bulk operations (Sprint 50 C2).

Implements [ADR-028 §"Governance" rule 2](../../../docs/adr/028-tags-as-first-class-entity.md):
"Every bulk op is dry-run-first with a confirmation token.
``POST …?dry_run=true`` returns ``{ affected, sample, token, expires_in }``;
``POST …?confirm=<token>`` applies. The token binds the preview to the
action — a second CSV cannot be confirmed with the first preview's token."

Designed as a small, generic primitive so the same machinery covers
``tags/import`` (C2), bulk ``PATCH`` / retire (C4), and any future
bulk endpoint the ADR adds (e.g. cross-tenant transfers in C3).

The token binds **(tenant_id, user_id, operation, content_hash)** —
- ``tenant_id`` so a token minted in tenant A cannot apply in tenant B,
- ``user_id`` so an operator cannot confirm another operator's preview
  (the two-person flow in C3 needs a separate ``approved_by`` lane —
  this guard is for accidental cross-confirmation, not policy),
- ``operation`` ("tags.import", "tags.patch", ...) so the same token
  cannot ride from one endpoint to another,
- ``content_hash`` so a *different* CSV cannot be confirmed with the
  same token — this is the core ADR guarantee.

In-process state. Single-replica TagPulse today; when we scale out
(Sprint 60+ horizontal API tier) this swaps for a Redis store —
the call surface (``mint`` + ``consume``) is intentionally narrow
to make that swap mechanical. Same trade-off as
:mod:`tagpulse.core.tag_import_rate_limit`.
"""

from __future__ import annotations

import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum

# 15 minutes. Long enough for an operator to eyeball the dry-run
# response, short enough that a forgotten preview tab can't be
# weaponised days later.
DEFAULT_TTL_SECONDS = 900


class ConfirmationOutcome(Enum):
    """Result of :meth:`BulkConfirmationTokenStore.consume`."""

    OK = "ok"
    NOT_FOUND = "not_found"
    EXPIRED = "expired"
    CONTENT_MISMATCH = "content_mismatch"
    TENANT_MISMATCH = "tenant_mismatch"
    USER_MISMATCH = "user_mismatch"
    OPERATION_MISMATCH = "operation_mismatch"


@dataclass(frozen=True)
class _Entry:
    tenant_id: uuid.UUID
    user_id: uuid.UUID | None
    operation: str
    content_hash: str
    expires_at: float


class BulkConfirmationTokenStore:
    """Thread-safe single-use token store with TTL eviction."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}

    def mint(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None,
        operation: str,
        content_hash: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        now: float | None = None,
    ) -> tuple[str, int]:
        """Mint a new token. Returns ``(token, expires_in_seconds)``."""
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        ts = time.monotonic() if now is None else now
        token = secrets.token_urlsafe(32)
        entry = _Entry(
            tenant_id=tenant_id,
            user_id=user_id,
            operation=operation,
            content_hash=content_hash,
            expires_at=ts + ttl_seconds,
        )
        with self._lock:
            self._purge_expired(ts)
            self._entries[token] = entry
        return token, ttl_seconds

    def consume(
        self,
        token: str,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None,
        operation: str,
        content_hash: str,
        now: float | None = None,
    ) -> ConfirmationOutcome:
        """Validate + consume the token in one atomic step.

        Single-use: a successful consume removes the token. Any
        mismatch leaves the token in place (so a typo doesn't burn
        the operator's preview).
        """
        ts = time.monotonic() if now is None else now
        with self._lock:
            entry = self._entries.get(token)
            if entry is None:
                return ConfirmationOutcome.NOT_FOUND
            if entry.expires_at < ts:
                # Lazy eviction.
                self._entries.pop(token, None)
                return ConfirmationOutcome.EXPIRED
            if entry.tenant_id != tenant_id:
                return ConfirmationOutcome.TENANT_MISMATCH
            if entry.user_id != user_id:
                return ConfirmationOutcome.USER_MISMATCH
            if entry.operation != operation:
                return ConfirmationOutcome.OPERATION_MISMATCH
            if entry.content_hash != content_hash:
                return ConfirmationOutcome.CONTENT_MISMATCH
            # All checks passed — single-use: drop the token.
            self._entries.pop(token, None)
            return ConfirmationOutcome.OK

    def reset(self) -> None:
        """Drop all tokens. Test-only."""
        with self._lock:
            self._entries.clear()

    def _purge_expired(self, ts: float) -> None:
        # Caller holds the lock.
        expired = [t for t, e in self._entries.items() if e.expires_at < ts]
        for t in expired:
            self._entries.pop(t, None)


BULK_CONFIRMATION_TOKENS = BulkConfirmationTokenStore()
