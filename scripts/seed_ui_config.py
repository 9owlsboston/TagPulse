#!/usr/bin/env python3
"""Apply the WM-facing UI presentation config to the demo tenant.

Sprint 60 (ADR-032) shipped the configurable-UI *mechanism* (the four-layer
``GET/PUT /ui-config`` family) and the UI consumes the ``labels`` / ``nav`` /
``cards`` / ``theme`` / ``columns`` / ``tables`` leaves. This shim closes the
second half of the sprint: it **applies the concrete WM-facing values** —
:data:`tagpulse.services.ui_config.WM_DEMO_PRESENTATION` (the ``Device`` ->
``Reader`` label skin plus the nav-simplification, hidden cards, sparkline card
style, TID/raw-memory advanced columns, and newest-first sort) — to a demo
tenant by ``PUT``-ing it to the **tenant-default** layer, so the demo actually
renders the WM persona instead of merely being *able* to.

The values are imported from the canonical registry rather than hardcoded here,
so the demo can never drift from the backend's source of truth.

Auth: the admin API key (Bearer) resolves to the tenant's admin user, which
``PUT /ui-config/tenant`` requires (``require_role("admin")``).

Idempotent: ``PUT /ui-config/tenant`` replaces the tenant-default leaves
wholesale, so re-running converges to the same config.

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

from tagpulse.services.ui_config import WM_DEMO_PRESENTATION, WM_LABEL_SKIN  # noqa: E402

API_URL = os.environ.get("TAGPULSE_API_URL", "http://localhost:8000").rstrip("/")


def _headers(tenant_id: str, api_key: str) -> dict[str, str]:
    return {
        "X-Tenant-ID": tenant_id,
        "Authorization": f"Bearer {api_key}",
    }


def apply_wm_presentation(tenant_id: str, api_key: str) -> dict[str, object]:
    """PUT the WM presentation config to the tenant-default layer.

    Pushes the full ``WM_DEMO_PRESENTATION`` (label skin + nav/cards/theme/
    columns/tables) the UI now consumes, returning the resolved document.
    Raises ``SystemExit`` on a non-2xx response so the composer fails fast.
    """
    body = dict(WM_DEMO_PRESENTATION)
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

    resolved: dict[str, object] = resp.json()
    # Verify the headline WM label skin took effect in the resolved document.
    labels = resolved.get("labels", {})
    mismatched = {
        k: v for k, v in WM_LABEL_SKIN.items() if not isinstance(labels, dict) or labels.get(k) != v
    }
    if mismatched:
        print(
            f"  FATAL: WM skin did not resolve as expected: {mismatched}",
            file=sys.stderr,
        )
        sys.exit(1)
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True, help="Target tenant UUID")
    parser.add_argument("--api-key", required=True, help="Admin API key for the tenant")
    args = parser.parse_args()

    resolved = apply_wm_presentation(args.tenant_id, args.api_key)
    labels = resolved.get("labels", {})
    skin = ", ".join(
        f"{k}->{labels[k]}"
        for k in sorted(WM_LABEL_SKIN)
        if isinstance(labels, dict) and k in labels
    )
    leaves = ", ".join(sorted(WM_DEMO_PRESENTATION))
    print(
        f"  applied WM presentation to tenant {args.tenant_id}: "
        f"leaves [{leaves}]; label skin {skin}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
