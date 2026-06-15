#!/usr/bin/env python3
"""Apply the SuperMart brand kit to the WM-facing demo tenant.

The branding-logo-upload chore shipped two-logo branding (a full wordmark for
the expanded sidebar and a square mark for the collapsed rail), stored inline
as base64 ``data:`` URLs on the ``tenants`` row. This shim makes the demo
tenant come up **pre-branded** with the SuperMart kit instead of the bare
display-name fallback.

The two source images live in ``scripts/assets/`` (committed, already trimmed
and downscaled to retina): ``demo-supermart-logo-full.png`` (~200x32 wordmark)
and ``demo-supermart-logo-collapsed.png`` (32x32 mark). They are read at
runtime and encoded into ``data:image/png;base64,`` URLs, so the brand kit
travels with the repo — no blob storage, no external hosting.

Auth: the admin API key (Bearer) resolves to the tenant's admin user, which
``PATCH /tenant/branding`` requires (``require_role("admin")``).

Idempotent: ``PATCH /tenant/branding`` is last-writer-wins per field, so
re-running converges to the same branding.

Usage:
    python scripts/seed_branding.py \\
        --tenant-id <UUID> \\
        --api-key <KEY>

Local/dev demo tooling. Not part of the production ingest path.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
ASSETS_DIR = REPO / "scripts" / "assets"

DEFAULT_FULL_LOGO = ASSETS_DIR / "demo-supermart-logo-full.png"
DEFAULT_COLLAPSED_LOGO = ASSETS_DIR / "demo-supermart-logo-collapsed.png"

# Teal accent shared with the SuperMart mark (the storefront awning / "M").
DEMO_BRAND_COLOR = "#14B8A6"

API_URL = os.environ.get("TAGPULSE_API_URL", "http://localhost:8000").rstrip("/")


def _data_url(path: Path) -> str:
    """Encode an image file as a ``data:image/...;base64,`` URL."""
    suffix = path.suffix.lower().lstrip(".")
    mime = {"png": "png", "jpg": "jpeg", "jpeg": "jpeg", "svg": "svg+xml", "webp": "webp"}.get(
        suffix, "png"
    )
    raw = path.read_bytes()
    return f"data:image/{mime};base64,{base64.b64encode(raw).decode()}"


def apply_branding(
    tenant_id: str,
    api_key: str,
    *,
    full_logo: Path,
    collapsed_logo: Path,
    brand_color: str,
) -> dict[str, object]:
    """PATCH the SuperMart brand kit onto the tenant. Fails fast on non-2xx."""
    body = {
        "logo_url": _data_url(full_logo),
        "logo_collapsed_url": _data_url(collapsed_logo),
        "brand_color": brand_color,
    }
    url = f"{API_URL}/tenant/branding"
    headers = {"X-Tenant-ID": tenant_id, "Authorization": f"Bearer {api_key}"}
    try:
        resp = httpx.patch(url, headers=headers, json=body, timeout=30.0)
    except httpx.HTTPError as exc:
        print(f"  FATAL: PATCH {url} failed: {exc}", file=sys.stderr)
        sys.exit(1)

    if resp.status_code != 200:
        print(
            f"  FATAL: PATCH /tenant/branding returned {resp.status_code}: {resp.text}",
            file=sys.stderr,
        )
        sys.exit(1)

    resolved: dict[str, object] = resp.json()
    if not resolved.get("logo_url") or not resolved.get("logo_collapsed_url"):
        print(
            f"  FATAL: branding did not resolve both logos: {sorted(resolved)}",
            file=sys.stderr,
        )
        sys.exit(1)
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True, help="Target tenant UUID")
    parser.add_argument("--api-key", required=True, help="Admin API key for the tenant")
    parser.add_argument(
        "--full-logo",
        type=Path,
        default=DEFAULT_FULL_LOGO,
        help="Full/expanded wordmark image (default: bundled SuperMart asset)",
    )
    parser.add_argument(
        "--collapsed-logo",
        type=Path,
        default=DEFAULT_COLLAPSED_LOGO,
        help="Collapsed/square mark image (default: bundled SuperMart asset)",
    )
    parser.add_argument(
        "--brand-color",
        default=DEMO_BRAND_COLOR,
        help=f"Hex accent color (default: {DEMO_BRAND_COLOR})",
    )
    args = parser.parse_args()

    for label, path in (("full", args.full_logo), ("collapsed", args.collapsed_logo)):
        if not path.is_file():
            print(f"  FATAL: {label} logo not found: {path}", file=sys.stderr)
            return 1

    resolved = apply_branding(
        args.tenant_id,
        args.api_key,
        full_logo=args.full_logo,
        collapsed_logo=args.collapsed_logo,
        brand_color=args.brand_color,
    )
    full_kb = len(str(resolved.get("logo_url", ""))) / 1024
    coll_kb = len(str(resolved.get("logo_collapsed_url", ""))) / 1024
    print(
        f"  applied SuperMart brand kit to tenant {args.tenant_id}: "
        f"full logo {full_kb:.1f}KB, collapsed logo {coll_kb:.1f}KB, "
        f"accent {resolved.get('brand_color')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
