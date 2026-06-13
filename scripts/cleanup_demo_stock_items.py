#!/usr/bin/env python3
"""Delete orphaned stock_items for the non-perishable demo SKUs.

When a seeder POSTs stock_items with a serial scheme that does not match the
read stream (see the "serial alignment" sim gap in ``docs/backlog.md``), the
stock_items never get bound by reads and show up as zone ``unassigned``. This
helper force-deletes every stock_item for the five non-perishable SKUs so the
seeder can re-materialize them cleanly.

Usage:
    export TAGPULSE_API_KEY=tp_demo-wm-dc_...
    python scripts/cleanup_demo_stock_items.py

Destructive (hard delete via ``?force=true``) but scoped to the five demo SKUs
only. Local/dev demo tooling — do not run against production tenants.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx

API = os.environ.get("TAGPULSE_API_URL", "http://localhost:8000").rstrip("/")
KEY = os.environ["TAGPULSE_API_KEY"]
DEMO_TENANT_SLUG = os.environ.get("DEMO_TENANT_SLUG", "demo-wm-dc")
TID = os.environ.get("TAGPULSE_TENANT_ID") or str(
    uuid.uuid5(uuid.NAMESPACE_DNS, f"{DEMO_TENANT_SLUG}.tagpulse.local")
)
H = {"X-Tenant-ID": TID, "Authorization": f"Bearer {KEY}"}

SKUS = {
    "SKU-SHOE-RUN-SZ10",
    "SKU-JEANS-32X32",
    "SKU-TV-55-4K",
    "SKU-SPKR-BT",
    "SKU-TOWEL-BATH-6PK",
}


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        items = payload.get("items", payload)
        return items if isinstance(items, list) else []
    return []


def main() -> None:
    deleted = 0
    with httpx.Client(timeout=10.0) as client:
        prods = _rows(client.get(f"{API}/products?limit=100", headers=H).json())
        targets = [p for p in prods if p["sku"] in SKUS]
        for p in targets:
            items = _rows(
                client.get(f"{API}/stock-items?product_id={p['id']}&limit=1000", headers=H).json()
            )
            for it in items:
                resp = client.delete(f"{API}/stock-items/{it['id']}?force=true", headers=H)
                if resp.status_code == 204:
                    deleted += 1
            print(f"  {p['sku']}: cleared {len(items)} stock items")
    print(f"Deleted {deleted} orphaned stock items.")


if __name__ == "__main__":
    main()
