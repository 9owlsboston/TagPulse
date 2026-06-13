#!/usr/bin/env python3
"""Verify the demo catalog: products, per-zone stock for the shoe SKU, and the
Lot Expiry Queue.

Quick read-only sanity check after running ``seed_nonperishable_skus.py`` (or
the perishable simulators). Confirms:
  * every product is listed with its category,
  * the size-10 shoe SKU has on-hand stock distributed across zones,
  * non-perishable lots do **not** leak into the Lot Expiry Queue (they have
    no ``expires_at``, so the queue should never list them).

Usage:
    export TAGPULSE_API_KEY=tp_demo-wm-dc_...
    python scripts/verify_catalog.py

Read-only; safe to run anytime. Local/dev demo tooling.
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

NON_PERISHABLE_LOTS = {
    "RCV-SHOE-2606",
    "RCV-JEANS-2606",
    "RCV-TV-2606",
    "RCV-SPKR-2606",
    "RCV-TOWEL-2606",
}


def _get(client: httpx.Client, path: str) -> Any:
    r = client.get(f"{API}{path}", headers=H)
    r.raise_for_status()
    return r.json()


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        items = payload.get("items", payload)
        return items if isinstance(items, list) else []
    return []


def main() -> None:
    with httpx.Client(timeout=10.0) as client:
        prods = _rows(_get(client, "/products?limit=100"))
        print(f"{len(prods)} products")
        for p in prods:
            sku = p["sku"]
            cat = p.get("category", "") or ""
            print(f"  {sku:22} {cat:20} {p['name']}")

        print("\n== stock-levels for the shoe SKU ==")
        shoe = next((p for p in prods if p["sku"] == "SKU-SHOE-RUN-SZ10"), None)
        levels = _rows(_get(client, "/stock-levels"))
        zones = {str(z["id"]): z["name"] for z in _rows(_get(client, "/zones"))}
        total = 0
        for row in levels:
            if shoe and str(row.get("product_id")) == str(shoe["id"]):
                zid = row.get("zone_id")
                zname = zones.get(str(zid), "unassigned" if zid is None else str(zid))
                qty = row.get("quantity") or 0
                total += qty
                print(f"  {str(zname):18} qty={qty}")
        print(f"  -> shoe total on-hand: {total}")

        print("\n== lot expiry queue (within 3650d) ==")
        exp = _rows(_get(client, "/lots?expiring_within_days=3650&limit=1000"))
        print(f"  {len(exp)} lots with expiry:")
        leaked = []
        for entry in exp:
            code = entry.get("lot_code")
            print("   ", code, "exp:", entry.get("expires_at"))
            if code in NON_PERISHABLE_LOTS:
                leaked.append(code)
        print(f"  -> non-perishable lots leaked into expiry queue: {leaked or 'NONE (correct)'}")


if __name__ == "__main__":
    main()
