"""Sprint 22 A4: in-process per-(tenant, route_class) rate limiter.

Token-bucket implementation. One bucket per ``(tenant_id, route_class)``
key; refilled at ``limit / 60`` tokens per second up to ``limit`` capacity.
Cross-replica drift is accepted for v1 — the next iteration moves this
to a Redis backend behind the same protocol (ADR-016 §5).

Route classification (cheap path-prefix + method match — runs on every
request, must be O(1)):

* ``ingest`` — ``POST /tag-reads``, ``POST /telemetry`` ingestion paths.
* ``admin`` — anything under ``/admin``.
* ``write`` — ``POST``/``PATCH``/``PUT``/``DELETE`` not classified above.
* ``read`` — ``GET``/``HEAD`` (the default).

Per-tenant overrides come from ``tenants.rate_limit_overrides`` JSONB
(migration 033) and are cached in-process for ``_OVERRIDE_TTL_S`` to
avoid a DB round-trip per request. The cache is refreshed lazily.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from tagpulse.core.config import settings

logger = logging.getLogger(__name__)

RouteClass = Literal["ingest", "read", "write", "admin"]

_OVERRIDE_TTL_S = 30.0


@dataclass(slots=True)
class _Bucket:
    """A single tenant's token bucket for one route class."""

    tokens: float
    last_refill: float
    capacity: int

    def consume(self, now: float, refill_per_sec: float) -> bool:
        """Try to consume one token; return False if empty."""
        elapsed = now - self.last_refill
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * refill_per_sec)
            self.last_refill = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class RateLimiter:
    """In-process token-bucket rate limiter."""

    def __init__(self) -> None:
        self._buckets: dict[tuple[UUID, RouteClass], _Bucket] = {}
        self._overrides: dict[UUID, tuple[float, dict[str, int]]] = {}
        self._lock = asyncio.Lock()

    def _global_limit(self, route_class: RouteClass) -> int:
        if route_class == "ingest":
            return settings.rate_limit_ingest_per_min
        if route_class == "admin":
            return settings.rate_limit_admin_per_min
        if route_class == "write":
            return settings.rate_limit_write_per_min
        return settings.rate_limit_read_per_min

    async def _resolve_limit(self, tenant_id: UUID, route_class: RouteClass) -> int:
        """Return effective per-minute limit for this (tenant, class)."""
        cached = self._overrides.get(tenant_id)
        now = time.monotonic()
        if cached is None or now - cached[0] > _OVERRIDE_TTL_S:
            override_map = await self._fetch_override(tenant_id)
            self._overrides[tenant_id] = (now, override_map)
        else:
            override_map = cached[1]
        if route_class in override_map:
            return override_map[route_class]
        return self._global_limit(route_class)

    async def _fetch_override(self, tenant_id: UUID) -> dict[str, int]:
        """Lazy DB lookup for tenants.rate_limit_overrides."""
        from sqlalchemy import select

        from tagpulse.models.database import TenantModel
        from tagpulse.repositories.timescaledb.session import async_session_factory

        try:
            async with async_session_factory() as session:
                stmt = select(TenantModel.rate_limit_overrides).where(TenantModel.id == tenant_id)
                result = await session.execute(stmt)
                row = result.scalar_one_or_none()
        except Exception:
            logger.exception("rate_limit override lookup failed for tenant %s", tenant_id)
            return {}
        if not isinstance(row, dict):
            return {}
        out: dict[str, int] = {}
        for key in ("ingest", "read", "write", "admin"):
            value = row.get(key)
            if isinstance(value, int) and value > 0:
                out[key] = value
        return out

    async def check(self, tenant_id: UUID, route_class: RouteClass) -> tuple[bool, int]:
        """Try to consume a token. Returns (allowed, effective_limit)."""
        limit = await self._resolve_limit(tenant_id, route_class)
        refill_per_sec = limit / 60.0
        now = time.monotonic()
        async with self._lock:
            key = (tenant_id, route_class)
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=float(limit), last_refill=now, capacity=limit)
                self._buckets[key] = bucket
            elif bucket.capacity != limit:
                # Limit changed via override flip — keep current tokens
                # but cap to new capacity, update refill.
                bucket.capacity = limit
                bucket.tokens = min(bucket.tokens, float(limit))
            allowed = bucket.consume(now, refill_per_sec)
        return allowed, limit

    def invalidate(self, tenant_id: UUID) -> None:
        """Drop cached overrides for a tenant (called from PATCH /tenant/config)."""
        self._overrides.pop(tenant_id, None)

    def reset(self) -> None:
        """Test-only: clear all state."""
        self._buckets.clear()
        self._overrides.clear()


# Module-level singleton consumed by the FastAPI middleware.
RATE_LIMITER = RateLimiter()


def classify_route(method: str, path: str) -> RouteClass:
    """Map (method, path) → route_class. Cheap; runs per request."""
    if path.startswith("/admin"):
        return "admin"
    if method == "POST" and (
        path.startswith("/tag-reads") or path.startswith("/telemetry") or path.startswith("/ingest")
    ):
        return "ingest"
    if method in {"GET", "HEAD"}:
        return "read"
    if method in {"POST", "PATCH", "PUT", "DELETE"}:
        return "write"
    return "read"


# Paths that bypass rate limiting entirely. Health/metrics need to stay
# probable from cluster-internal probes that don't carry X-Tenant-ID.
_BYPASS_PREFIXES = (
    "/health",
    "/metrics",
    "/security/csp-report",
    "/auth/login",
    "/docs",
    "/openapi",
    "/redoc",
)


async def rate_limit_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """ASGI middleware: enforce per-(tenant, route_class) limits."""
    if not settings.rate_limit_enabled:
        return await call_next(request)
    path = request.url.path
    if any(path.startswith(p) for p in _BYPASS_PREFIXES):
        return await call_next(request)
    tenant_header = request.headers.get("X-Tenant-ID")
    if not tenant_header:
        # Unauthenticated paths (e.g. /auth/login) handle their own
        # limits (login_rate_limit). Anything else without a tenant
        # header has no quota anchor and is allowed; tenant-scoped
        # routes will reject the request later for a different reason.
        return await call_next(request)
    try:
        tenant_id = UUID(tenant_header)
    except ValueError:
        return await call_next(request)
    route_class = classify_route(request.method, path)
    allowed, limit = await RATE_LIMITER.check(tenant_id, route_class)
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={
                "detail": (f"Rate limit exceeded for {route_class} (limit={limit}/min)."),
                "route_class": route_class,
                "limit_per_min": limit,
            },
            headers={"Retry-After": "60"},
        )
    return await call_next(request)
