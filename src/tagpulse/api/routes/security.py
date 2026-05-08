"""Security-adjacent public endpoints.

Sprint 25 A3: ``POST /security/csp-report`` receives Content Security Policy
violation reports from the SPA and emits structured logs + a Prometheus
counter. The endpoint is unauthenticated (browsers do not send credentials on
report POSTs by design) and per-IP rate-limited at 10/min so a noisy browser
extension cannot DoS the api.

Backed by the existing rate-limit bypass list (``/security/csp-report`` is
opted out of the per-tenant limiter in :mod:`tagpulse.core.rate_limit`); the
per-IP limit lives in this module because the tenant-keyed limiter cannot
attribute requests without an authenticated principal.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import Counter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["security"])

# Sprint 25 A3: violations counter labeled by directive. Cardinality is bounded
# by the CSP spec (~20 directives), safe for Prometheus.
csp_violations_total = Counter(
    "tagpulse_csp_violations_total",
    "Count of CSP violation reports received from browsers, by directive.",
    labelnames=("directive",),
)

# Per-IP token bucket. 10 reports / 60s rolling window. In-process; same
# trade-off as the Sprint 22 A4 rate limiter — a multi-replica api tier may
# admit up to 10*N reports/min cluster-wide, which is fine for this endpoint.
_RATE_LIMIT_MAX = 10
_RATE_LIMIT_WINDOW_S = 60.0
_recent_reports: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=_RATE_LIMIT_MAX))


def _client_ip(request: Request) -> str:
    """Best-effort client IP. Honors ``X-Forwarded-For`` first hop."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _allow(ip: str) -> bool:
    """Sliding-window admission for ``ip``."""
    now = time.monotonic()
    bucket = _recent_reports[ip]
    while bucket and now - bucket[0] > _RATE_LIMIT_WINDOW_S:
        bucket.popleft()
    if len(bucket) >= _RATE_LIMIT_MAX:
        return False
    bucket.append(now)
    return True


def _normalize_report(payload: Any) -> list[dict[str, Any]]:
    """Flatten the two browser report shapes into a list of report dicts.

    * ``application/csp-report`` (Chromium/Safari legacy):
      ``{"csp-report": {...}}`` — one report per request.
    * ``application/reports+json`` (Reporting API): an array of envelopes
      ``[{"type": "csp-violation", "body": {...}}, ...]``.

    Returns a list of inner report dicts (possibly empty). Anything that
    doesn't match either shape becomes a single best-effort dict so the
    structured log still captures *something*.
    """
    if isinstance(payload, dict) and "csp-report" in payload:
        inner = payload["csp-report"]
        return [inner] if isinstance(inner, dict) else []
    if isinstance(payload, list):
        out: list[dict[str, Any]] = []
        for env in payload:
            if not isinstance(env, dict):
                continue
            body = env.get("body")
            if isinstance(body, dict):
                out.append(body)
        return out
    if isinstance(payload, dict):
        return [payload]
    return []


def _directive(report: dict[str, Any]) -> str:
    """Extract the violated directive name across both report shapes."""
    raw = (
        report.get("violated-directive")
        or report.get("effective-directive")
        or report.get("effectiveDirective")
        or report.get("violatedDirective")
        or "unknown"
    )
    # Browsers emit e.g. ``script-src-elem 'self' …``; keep just the directive.
    return str(raw).split()[0] if raw else "unknown"


@router.post("/security/csp-report", include_in_schema=False)
async def csp_report(request: Request) -> Response:
    """Receive browser CSP violation reports.

    Returns ``204 No Content`` on success and ``429`` when the per-IP limit is
    exceeded. The body is logged at WARN with structured fields so operators
    can correlate violations across deployments.
    """
    ip = _client_ip(request)
    if not _allow(ip):
        return JSONResponse(
            {"detail": "rate limit exceeded"},
            status_code=429,
        )

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 — browsers occasionally send malformed JSON
        logger.warning("csp_report.invalid_json", extra={"client_ip": ip})
        return Response(status_code=204)

    reports = _normalize_report(payload)
    if not reports:
        logger.warning(
            "csp_report.empty_or_unknown_shape",
            extra={"client_ip": ip, "payload_type": type(payload).__name__},
        )
        return Response(status_code=204)

    for report in reports:
        directive = _directive(report)
        csp_violations_total.labels(directive=directive).inc()
        logger.warning(
            "csp.violation",
            extra={
                "client_ip": ip,
                "blocked_uri": report.get("blocked-uri") or report.get("blockedURL"),
                "document_uri": report.get("document-uri") or report.get("documentURL"),
                "violated_directive": directive,
                "source_file": report.get("source-file") or report.get("sourceFile"),
                "line_number": report.get("line-number") or report.get("lineNumber"),
                "column_number": report.get("column-number") or report.get("columnNumber"),
                "user_agent": request.headers.get("user-agent"),
            },
        )

    return Response(status_code=204)
