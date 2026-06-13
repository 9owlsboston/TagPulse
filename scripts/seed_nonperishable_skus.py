#!/usr/bin/env python3
"""Seed non-perishable general-merchandise SKUs into the demo tenant.

The built-in simulator catalog (``simulate_inventory.CATALOG``) is all
cold-chain perishables — vaccine, milk, yogurt, cheese. A real distribution
centre floor is mostly *general merchandise*: apparel, footwear, electronics,
home goods, which have **no expiry**. This seeder adds those SKUs so the demo
UI shows a realistic mixed catalog and walkthroughs like "where are the
size-10 shoes?" have a concrete SKU to click through.

How it works (reuses ``scripts/simulate_inventory.py`` helpers):
  1. Ensure inventory mode + Boston DC site / 4 zones / 4 readers (idempotent).
  2. Create products (``unit=each``) with GTINs the server derives from SGTIN.
  3. Create one lot per product **with no ``expires_at``** — a receiving batch
     / PO. Non-perishable lots never appear in the Lot Expiry Queue (which
     filters on ``expires_at IS NOT NULL``), which is exactly right.
  4. POST /stock-items directly (``binding_value`` = decoded SGTIN URI). This
     works around the latent ingest auto-create gate — see
     ``seed_stock_items.py`` and ``docs/backlog.md`` (Post-Sprint-58 cluster).
  5. Stream zone reads so Stock Levels + Stock Movements populate.

Idempotent: re-running reuses existing products, lots, and stock_items.

Usage:
    export TAGPULSE_API_KEY=tp_demo-wm-dc_...      # required
    # optional overrides (defaults shown):
    #   TAGPULSE_API_URL=http://localhost:8000
    #   DEMO_TENANT_SLUG=demo-wm-dc                 # tenant id derived via uuid5
    #   TAGPULSE_TENANT_ID=<uuid>                   # explicit override
    #   DURATION=90    TICK=0.3
    python scripts/seed_nonperishable_skus.py

Local/dev demo tooling. Not part of the production ingest path.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "src"))

import simulate_inventory as si  # noqa: E402

from tagpulse.rfid.epc import decode_epc_hex  # noqa: E402

API = os.environ.get("TAGPULSE_API_URL", "http://localhost:8000").rstrip("/")
KEY = os.environ["TAGPULSE_API_KEY"]
DEMO_TENANT_SLUG = os.environ.get("DEMO_TENANT_SLUG", "demo-wm-dc")
TID = os.environ.get("TAGPULSE_TENANT_ID") or str(
    uuid.uuid5(uuid.NAMESPACE_DNS, f"{DEMO_TENANT_SLUG}.tagpulse.local")
)
DURATION = float(os.environ.get("DURATION", "90"))
TICK = float(os.environ.get("TICK", "0.3"))

si._API_KEY = KEY
H = {"X-Tenant-ID": TID, "Authorization": f"Bearer {KEY}"}


# Non-perishable general-merchandise catalog. item_ref uses the 3xxxxx range
# to avoid GTIN collisions with the perishable catalog (1xxxxx / 2xxxxx).
# expires_in_days is unused here — lots are created WITHOUT an expiry below.
NON_PERISHABLE: list[si.CatalogItem] = [
    si.CatalogItem(
        sku="SKU-SHOE-RUN-SZ10",
        name="Men's Running Shoe Size 10",
        category="apparel/footwear",
        item_ref="300001",
        lot_code="RCV-SHOE-2606",
        expires_in_days=0,
        units=14,
    ),
    si.CatalogItem(
        sku="SKU-JEANS-32X32",
        name="Men's Jeans 32x32",
        category="apparel",
        item_ref="300002",
        lot_code="RCV-JEANS-2606",
        expires_in_days=0,
        units=10,
    ),
    si.CatalogItem(
        sku="SKU-TV-55-4K",
        name='55" 4K Smart TV',
        category="electronics",
        item_ref="300003",
        lot_code="RCV-TV-2606",
        expires_in_days=0,
        units=6,
    ),
    si.CatalogItem(
        sku="SKU-SPKR-BT",
        name="Portable Bluetooth Speaker",
        category="electronics",
        item_ref="300004",
        lot_code="RCV-SPKR-2606",
        expires_in_days=0,
        units=8,
    ),
    si.CatalogItem(
        sku="SKU-TOWEL-BATH-6PK",
        name="Bath Towel 6-pack",
        category="home",
        item_ref="300005",
        lot_code="RCV-TOWEL-2606",
        expires_in_days=0,
        units=10,
    ),
]


def _seed_products_and_lots(client: httpx.Client) -> list[si.CatalogItem]:
    """Create products + no-expiry lots. Returns items with ids filled in."""
    products = {p["sku"]: p for p in client.get(f"{API}/products", headers=H).json()}
    seeded: list[si.CatalogItem] = []
    for item in NON_PERISHABLE:
        item.gtin = si._gtin14(si.COMPANY_PREFIX, item.item_ref)

        product = products.get(item.sku)
        if product is None:
            r = client.post(
                f"{API}/products",
                headers=H,
                json={
                    "sku": item.sku,
                    "gtin": item.gtin,
                    "name": item.name,
                    "category": item.category,
                    "unit": "each",
                },
            )
            if r.status_code != 201:
                print(f"  FAIL product {item.sku}: {r.status_code} {r.text}")
                continue
            product = r.json()
            print(f"  Created product: {item.sku}  ({item.name})")
        else:
            print(f"  Reusing product: {item.sku}")
            if product.get("gtin") != item.gtin:
                r = client.patch(
                    f"{API}/products/{product['id']}",
                    headers=H,
                    json={"gtin": item.gtin},
                )
                if r.status_code == 200:
                    product = r.json()
                    print(f"    Healed GTIN -> {item.gtin}")
        item.product_id = product["id"]

        # Lot WITHOUT expiry (a receiving batch / PO). No expires_at => never
        # shows in the Lot Expiry Queue, which is correct for general merch.
        lots = client.get(f"{API}/products/{item.product_id}/lots", headers=H).json()
        lot = next((lot for lot in lots if lot["lot_code"] == item.lot_code), None)
        if lot is None:
            r = client.post(
                f"{API}/products/{item.product_id}/lots",
                headers=H,
                json={"lot_code": item.lot_code},  # no expires_at
            )
            if r.status_code != 201:
                print(f"  FAIL lot {item.lot_code}: {r.status_code} {r.text}")
                continue
            lot = r.json()
            print(f"  Created lot: {item.lot_code} (no expiry)")
        else:
            print(f"  Reusing lot: {item.lot_code}")
        item.lot_id = lot["id"]
        seeded.append(item)
    return seeded


def _materialize_stock_items(client: httpx.Client, catalog: list[si.CatalogItem]) -> None:
    created = existing = failed = 0
    for product_idx, item in enumerate(catalog):
        # MUST match _build_units' scheme: (product_idx+1)*100_000 + unit_idx.
        # The streamed reads use that serial, so the EPC/binding_value here has
        # to be identical or the reads never match these stock items (leaving
        # them "unassigned"). No perishable collision: item_ref (3xxxxx) differs.
        base = (product_idx + 1) * 100_000
        for unit_idx in range(item.units):
            serial = base + unit_idx
            epc_hex = si._sgtin96_hex(si.COMPANY_PREFIX, item.item_ref, serial)
            _scheme, decoded = decode_epc_hex(epc_hex)
            uri = decoded.get("uri") if isinstance(decoded, dict) else None
            if not uri:
                failed += 1
                continue
            body = {
                "product_id": item.product_id,
                "lot_id": item.lot_id,
                "binding_value": uri,
                "binding_kind": "epc",
            }
            r = client.post(f"{API}/stock-items", headers=H, json=body)
            if r.status_code == 201:
                created += 1
            elif r.status_code == 409:
                existing += 1
            else:
                failed += 1
                if failed <= 3:
                    print(f"  FAIL {uri}: {r.status_code} {r.text}")
            time.sleep(0.25)  # stay under 300/min write limit
        print(f"  {item.sku}: stock items done")
    print(f"  Totals: created={created} existing={existing} failed={failed}")


def main() -> None:
    with httpx.Client(timeout=10.0) as client:
        print("Step 0: inventory mode")
        si._ensure_inventory_mode(client, TID)

        print("\nStep 1: site + zone readers (Boston DC)")
        site_id, devices = si._seed_site_and_devices(client, TID)

        print("\nStep 2: reader-bound zones")
        si._seed_zones(client, TID, site_id, devices)

        print("\nStep 3: non-perishable products + no-expiry lots")
        catalog = _seed_products_and_lots(client)
        if not catalog:
            print("No catalog seeded — aborting.")
            sys.exit(1)

        print("\nStep 4: materialize stock items (direct POST workaround)")
        _materialize_stock_items(client, catalog)

        print(f"\nStep 5: stream zone reads over {DURATION:.0f}s")
        units = si._build_units(catalog, duration=DURATION)
        sent, fail = si._run_pipeline(client, TID, devices, units, DURATION, TICK)
        print(f"\nDone: {sent} reads sent, {fail} failed across {len(units)} units.")
        print("  UI: Inventory → Products → search 'shoe' (SKU/GTIN/name search)")
        print("      Inventory → Stock Levels (per-zone counts incl. new SKUs)")


if __name__ == "__main__":
    main()
