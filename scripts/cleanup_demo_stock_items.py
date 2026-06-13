#!/usr/bin/env python3
"""Retire stock_items for the non-perishable demo SKUs so the seeder can re-run.

When a seeder POSTs stock_items with a serial scheme that does not match the
read stream (see the "serial alignment" sim gap in ``docs/backlog.md``), the
stock_items never get bound by reads and show up as zone ``unassigned``. This
helper clears those units so ``seed_nonperishable_skus.py`` can re-materialize
them cleanly.

Why a *soft* retire instead of a hard delete? ``stock_movements`` has an
``ON DELETE RESTRICT`` FK to ``stock_items`` (migration 021, Sprint 15b — "the
ledger can never be orphaned"), so ``DELETE /stock-items/{id}?force=true`` 500s
for any unit that has ever moved through a zone. Instead we PATCH each unit to
``state=consumed``. That:

* removes it from on-hand / Stock Levels (terminal state), and
* frees its ``binding_value`` (EPC) for re-seeding — the partial unique index
  ``ix_stock_items_active_binding`` excludes ``consumed``/``expired``/``lost``
  (migration 020), so a fresh ``in_stock`` unit with the same EPC is allowed,
  while
* leaving the append-only movement ledger intact.

Usage:
    export TAGPULSE_API_KEY=tp_demo-wm-dc_...
    python scripts/cleanup_demo_stock_items.py

Scoped to the five non-perishable demo SKUs only. Local/dev demo tooling — do
not run against production tenants.
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

# states that already drop out of on-hand and free the binding (see module docs)
TERMINAL_STATES = {"consumed", "expired", "lost"}


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        items = payload.get("items", payload)
        return items if isinstance(items, list) else []
    return []


def main() -> None:
    retired = 0
    with httpx.Client(timeout=10.0) as client:
        prods = _rows(client.get(f"{API}/products?limit=100", headers=H).json())
        targets = [p for p in prods if p["sku"] in SKUS]
        for p in targets:
            items = _rows(
                client.get(f"{API}/stock-items?product_id={p['id']}&limit=1000", headers=H).json()
            )
            active = [it for it in items if it.get("state") not in TERMINAL_STATES]
            for it in active:
                resp = client.patch(
                    f"{API}/stock-items/{it['id']}",
                    headers=H,
                    json={"state": "consumed"},
                )
                if resp.status_code == 200:
                    retired += 1
            print(f"  {p['sku']}: retired {len(active)} stock items")
    print(f"Retired {retired} stock items (state=consumed; binding freed for reseed).")


if __name__ == "__main__":
    main()
