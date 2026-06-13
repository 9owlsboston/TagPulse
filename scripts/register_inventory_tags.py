#!/usr/bin/env python3
"""WORKAROUND: register the inventory simulator's SGTIN EPCs in the tags registry.

╔══════════════════════════════════════════════════════════════════════════╗
║ TEMPORARY WORKAROUND — remove once the latent ingest gate bug is fixed.    ║
║ Tracked in docs/backlog.md (Post-Sprint-58 demo-data chore cluster).       ║
╚══════════════════════════════════════════════════════════════════════════╝

Sprint 50 / ADR 028 gates stock_item auto-creation behind a tags-registry
lookup: ``IngestionService._enrich_with_inventory`` only auto-creates a
stock_item when the read's EPC already has a ``tags`` row in status
``registered`` / ``active``. ``simulate_inventory.py`` never registers its
EPCs, so Stock Levels stays empty.

This helper imports the simulator's own EPC encoder + catalog and registers a
generous serial range per product (uppercase hex), so a subsequent read stream
can materialize stock_items. It is an alternative to ``seed_stock_items.py``:
that one binds via the decoded URI, this one pre-registers the hex tag. Both
become unnecessary once the gate keys off ``identity.epc_hex`` correctly.

Usage:
    export TAGPULSE_API_KEY=tp_demo-wm-dc_...
    MAX_UNIT_IDX=199 python scripts/register_inventory_tags.py
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

import simulate_inventory as si  # noqa: E402

API = os.environ.get("TAGPULSE_API_URL", "http://localhost:8000").rstrip("/")
KEY = os.environ["TAGPULSE_API_KEY"]
DEMO_TENANT_SLUG = os.environ.get("DEMO_TENANT_SLUG", "demo-wm-dc")
TID = os.environ.get("TAGPULSE_TENANT_ID") or str(
    uuid.uuid5(uuid.NAMESPACE_DNS, f"{DEMO_TENANT_SLUG}.tagpulse.local")
)
MAX_UNIT_IDX = int(os.environ.get("MAX_UNIT_IDX", "199"))

H = {"X-Tenant-ID": TID, "Authorization": f"Bearer {KEY}"}


def main() -> None:
    created = existing = failed = 0
    with httpx.Client(timeout=10.0) as client:
        for product_idx, item in enumerate(si.CATALOG):
            for unit_idx in range(MAX_UNIT_IDX + 1):
                serial = (product_idx + 1) * 100_000 + unit_idx
                epc_hex = si._sgtin96_hex(si.COMPANY_PREFIX, item.item_ref, serial).upper()
                r = client.post(
                    f"{API}/tags",
                    headers=H,
                    json={"epc_hex": epc_hex, "source": "backfill"},
                )
                if r.status_code == 201:
                    created += 1
                elif r.status_code == 409:
                    existing += 1
                else:
                    failed += 1
                    if failed <= 3:
                        print(f"  FAIL {epc_hex}: {r.status_code} {r.text}")
                time.sleep(0.25)  # stay under the 300/min write rate limit
            print(f"  {item.sku}: done (running totals new={created} existing={existing})")
    print(f"\nDone: registered={created} already_existed={existing} failed={failed}")


if __name__ == "__main__":
    main()
