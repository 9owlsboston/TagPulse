#!/usr/bin/env python3
"""WORKAROUND seeder: materialize stock_items for the perishable catalog by
direct POST.

╔══════════════════════════════════════════════════════════════════════════╗
║ TEMPORARY WORKAROUND — remove once the latent ingest gate bug is fixed.    ║
║ Tracked in docs/backlog.md (Post-Sprint-58 demo-data chore cluster).       ║
╚══════════════════════════════════════════════════════════════════════════╝

The bug: the inventory auto-create gate (Sprint 50 / ADR 028) looks up the
*decoded* GS1 URI (``urn:epc:id:sgtin:…``) against the *hex-keyed*
``tags.epc_hex`` column, so it never matches and blocks every SGTIN
auto-create — Stock Levels stays empty even though reads are flowing.

The workaround: POST /stock-items with ``binding_value`` set to the decoded
URI the server derives from each EPC. Once the binding row exists, subsequent
reads match via ``get_active_by_binding`` and skip the gate, recording zone
observations + movements. Once the gate keys off ``identity.epc_hex`` (or
``get_by_epc`` accepts either form), the read stream alone will materialize
stock_items and this script becomes unnecessary.

Usage:
    export TAGPULSE_API_KEY=tp_demo-wm-dc_...
    UNITS=80 python scripts/seed_stock_items.py
    # then run a read stream, e.g. python scripts/simulate_inventory.py

Local/dev demo tooling.
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
UNITS = int(os.environ.get("UNITS", "80"))

H = {"X-Tenant-ID": TID, "Authorization": f"Bearer {KEY}"}


def main() -> None:
    # Scale catalog exactly like simulate_inventory --units does.
    catalog = list(si.CATALOG)
    default_total = sum(c.units for c in catalog)
    scale = UNITS / default_total
    scaled = [(c, max(1, round(c.units * scale))) for c in catalog]

    with httpx.Client(timeout=10.0) as client:
        products = {p["sku"]: p for p in client.get(f"{API}/products", headers=H).json()}
        lots_by_product: dict[str, dict[str, str]] = {}
        for lot in client.get(f"{API}/lots", headers=H).json():
            lots_by_product.setdefault(str(lot["product_id"]), {})[lot["lot_code"]] = str(lot["id"])

        created = existing = failed = 0
        for product_idx, (item, units) in enumerate(scaled):
            product = products.get(item.sku)
            if product is None:
                print(f"  SKIP {item.sku}: no product row")
                continue
            product_id = str(product["id"])
            lot_id = lots_by_product.get(product_id, {}).get(item.lot_code)
            for unit_idx in range(units):
                serial = (product_idx + 1) * 100_000 + unit_idx
                epc_hex = si._sgtin96_hex(si.COMPANY_PREFIX, item.item_ref, serial)
                _scheme, decoded = decode_epc_hex(epc_hex)
                uri = decoded.get("uri") if isinstance(decoded, dict) else None
                if not uri:
                    failed += 1
                    continue
                body = {
                    "product_id": product_id,
                    "binding_value": uri,
                    "binding_kind": "epc",
                }
                if lot_id:
                    body["lot_id"] = lot_id
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
            print(f"  {item.sku}: done (created={created} existing={existing})")

    print(f"\nDone: created={created} already_existed={existing} failed={failed}")


if __name__ == "__main__":
    main()
