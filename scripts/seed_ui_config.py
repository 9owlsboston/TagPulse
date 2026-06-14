#!/usr/bin/env python3
"""Apply the WM-facing UI presentation config to the demo tenant.

Sprint 60 (ADR-032) shipped the configurable-UI *mechanism* (the four-layer
``GET/PUT /ui-config`` family) and the UI now consumes the ``labels`` /
``nav`` / ``cards`` / ``theme`` leaves. This shim closes the second half of
the sprint: it **applies the one decided WM-facing value** — the
``Device`` -> ``Reader`` label skin (:data:`tagpulse.services.ui_config.WM_LABEL_SKIN`)
— to a demo tenant by ``PUT``-ing it to the **tenant-default** layer, so the
demo actually renders ``Reader`` instead of merely being *able* to.

The value is imported from the canonical registry rather than hardcoded here,
so the demo can never drift from the backend's source of truth.

Auth: the admin API key (Bearer) resolves to the tenant's admin user, which
``PUT /ui-config/tenant`` requires (``require_role("admin")``).

Idempotent: ``PUT /ui-config/tenant`` replaces the tenant-default leaves
wholesale, so re-running converges to the same skin.

Usage:
    python scripts/seed_ui_config.py \\
        --tenant-id <UUID> \\
        --api-key <KEY>

Local/dev demo tooling. Not part of the production ingest path.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from tagpulse.services.ui_config import WM_LABEL_SKIN  # noqa: E402

API_URL = os.environ.get("TAGPULSE_API_URL", "http://localhost:8000").rstrip("/")


def _headers(tenant_id: str, api_key: str) -> dict[str, str]:
    return {
        "X-Tenant-ID": tenant_id,
        "Authorization": f"Bearer {api_key}",
    }


def apply_wm_skin(tenant_id: str, api_key: str) -> dict[str, str]:
    """PUT the WM label skin to the tenant-default layer; return resolved labels.

    Raises ``SystemExit`` on a non-2xx response so the composer fails fast.
    """
    body = {"labels": dict(WM_LABEL_SKIN)}
    url = f"{API_URL}/ui-config/tenant"
    try:
        resp = httpx.put(url, headers=_headers(tenant_id, api_key), json=body, timeout=30.0)
    except httpx.HTTPError as exc:
        print(f"  FATAL: PUT {url} failed: {exc}", file=sys.stderr)
        sys.exit(1)

    if resp.status_code != 200:
        print(
            f"  FATAL: PUT /ui-config/tenant returned {resp.status_code}: {resp.text}",
            file=sys.stderr,
        )
        sys.exit(1)

    resolved = resp.json()
    labels: dict[str, str] = resolved.get("labels", {})
    # Verify every skinned term took effect in the resolved document.
    mismatched = {k: v for k, v in WM_LABEL_SKIN.items() if labels.get(k) != v}
    if mismatched:
        print(
            f"  FATAL: WM skin did not resolve as expected: {mismatched}",
            file=sys.stderr,
        )
        sys.exit(1)
    return labels


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True, help="Target tenant UUID")
    parser.add_argument("--api-key", required=True, help="Admin API key for the tenant")
    args = parser.parse_args()

    labels = apply_wm_skin(args.tenant_id, args.api_key)
    applied = ", ".join(f"{k}->{labels[k]}" for k in sorted(WM_LABEL_SKIN))
    print(f"  applied WM label skin to tenant {args.tenant_id}: {applied}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
