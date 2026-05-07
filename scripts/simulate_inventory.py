#!/usr/bin/env python3
"""Inventory tracking simulator (Sprint 15b Phase E).

End-to-end exercise of the inventory branch:

1. Seeds a small product catalog with valid GTIN-14s.
2. Creates one lot per product with an expires_at near the
   ``stock.expiring_within`` rule horizon, so the worker has something to
   alert on within a single run.
3. Registers a ``tag_data_mapping`` for ``lot_code`` so the ingestion
   service can infer the lot from the simulated tag_data payload.
4. Streams tag reads at one or more devices using SGTIN-96 EPCs whose
   company-prefix + item-ref decode to the catalog's GTINs (so the
   ingestion service auto-creates ``stock_item`` rows and emits zone
   transitions).

Usage:
    python scripts/simulate_inventory.py \
        --tenant-id <UUID> --devices 2 --interval 1.5 --duration 120

The script is idempotent: re-running re-uses any existing products / lots /
mapping with matching SKUs and lot_codes.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from datetime import UTC, datetime, timedelta

import httpx

API_URL = "http://localhost:8000"

# Optional bearer API key (admin/editor) — populated from --api-key or
# TAGPULSE_API_KEY env. Required for product/lot/device writes since Sprint 12.
_API_KEY: str | None = None


def _headers(tenant_id: str) -> dict[str, str]:
    h = {"X-Tenant-ID": tenant_id}
    if _API_KEY:
        h["Authorization"] = f"Bearer {_API_KEY}"
    return h

# A tiny synthetic catalog. GTIN-14s are computed below from
# (company_prefix, item_ref) using the standard mod-10 check digit.
COMPANY_PREFIX = "0614141"  # 7-digit GS1 prefix (test range)
ITEMS = [
    ("SKU-MILK-1L", "Milk 1L", "100001"),
    ("SKU-EGGS-12", "Eggs (12 pack)", "100002"),
    ("SKU-CHEESE-200", "Cheese 200g", "100003"),
]


def _gtin14(company_prefix: str, item_ref: str) -> str:
    """Compute GTIN-14 from indicator '0' + prefix + item_ref + checksum."""
    body = "0" + company_prefix + item_ref  # 13 digits
    if len(body) != 13 or not body.isdigit():
        raise ValueError(f"invalid gtin13 body: {body}")
    # Mod-10 check digit per GS1 §7.9.
    odds = sum(int(c) for c in body[-1::-2])  # rightmost first
    evens = sum(int(c) for c in body[-2::-2])
    total = odds * 3 + evens
    check = (10 - (total % 10)) % 10
    return body + str(check)


def _sgtin96_hex(company_prefix: str, item_ref: str, serial: int) -> str:
    """Encode an SGTIN-96 EPC as 24-hex-char string.

    Header=0x30, filter=001 (POS item), partition=5 (7-digit prefix +
    6-digit item_ref), then prefix + item_ref + 38-bit serial. The
    production decoder in ``tagpulse.rfid.epc`` will parse this back out.
    """
    if len(company_prefix) != 7 or len(item_ref) != 6:
        raise ValueError("test data assumes 7+6 partition")
    header = 0x30
    filter_value = 0b001
    partition = 0b101  # 7+6
    prefix = int(company_prefix)
    item = int(item_ref)
    serial &= (1 << 38) - 1
    bits = (
        (header << 88)
        | (filter_value << 85)
        | (partition << 82)
        | (prefix << 58)
        | (item << 38)
        | serial
    )
    return f"{bits:024x}"


def _seed_catalog(
    client: httpx.Client, tenant_id: str
) -> list[dict[str, str]]:
    """Create products + one lot per product + a tag_data_mapping."""
    headers = _headers(tenant_id)
    catalog: list[dict[str, str]] = []
    soon = datetime.now(UTC) + timedelta(days=3)

    # Existing products keyed by SKU.
    resp = client.get(
        f"{API_URL}/products",
        headers=headers,
        params={"limit": 1000},
    )
    existing = {p["sku"]: p for p in resp.json() if resp.status_code == 200}

    for sku, name, item_ref in ITEMS:
        gtin = _gtin14(COMPANY_PREFIX, item_ref)
        if sku in existing:
            product = existing[sku]
            print(f"  Reusing product: {sku} ({product['id']})")
        else:
            r = client.post(
                f"{API_URL}/products",
                headers=headers,
                json={
                    "sku": sku,
                    "gtin": gtin,
                    "name": name,
                    "unit": "each",
                },
            )
            if r.status_code != 201:
                print(f"  FAIL product {sku}: {r.status_code} {r.text}")
                continue
            product = r.json()
            print(f"  Created product: {sku} ({product['id']})")

        # Lot.
        lots_r = client.get(
            f"{API_URL}/products/{product['id']}/lots", headers=headers
        )
        lots = lots_r.json() if lots_r.status_code == 200 else []
        existing_lot = next(
            (lot for lot in lots if lot["lot_code"] == "LOT-A"), None
        )
        if existing_lot is None:
            lr = client.post(
                f"{API_URL}/products/{product['id']}/lots",
                headers=headers,
                json={
                    "lot_code": "LOT-A",
                    "expires_at": soon.isoformat(),
                },
            )
            if lr.status_code != 201:
                print(f"  FAIL lot for {sku}: {lr.status_code} {lr.text}")
                continue
            print(f"  Created lot LOT-A for {sku} (expires {soon.date()})")

        catalog.append(
            {
                "sku": sku,
                "product_id": product["id"],
                "company_prefix": COMPANY_PREFIX,
                "item_ref": item_ref,
                "gtin": gtin,
            }
        )

    # Register tenant-scope tag_data_mapping for lot_code so the ingestion
    # service auto-binds simulated reads to the LOT-A lot.
    mr = client.get(
        f"{API_URL}/tag-data-mappings",
        headers=headers,
        params={"scope_kind": "tenant"},
    )
    existing_map = mr.json() if mr.status_code == 200 else []
    if not any(m.get("semantic_field") == "lot_code" for m in existing_map):
        rm = client.post(
            f"{API_URL}/tag-data-mappings",
            headers=headers,
            json={
                "scope_kind": "tenant",
                "scope_id": None,
                "semantic_field": "lot_code",
                "tag_data_key": "lot",
            },
        )
        if rm.status_code == 201:
            print("  Registered tag_data_mapping: tag_data.lot -> lot_code")

    return catalog


def _seed_devices(
    client: httpx.Client, tenant_id: str, count: int
) -> list[dict[str, str]]:
    headers = _headers(tenant_id)
    resp = client.get(
        f"{API_URL}/device-registry", headers=headers, params={"limit": 1000}
    )
    existing = {d["name"]: d["id"] for d in resp.json()} if resp.status_code == 200 else {}
    devices: list[dict[str, str]] = []
    for i in range(count):
        name = f"Inv-Sim-Reader-{i + 1:02d}"
        if name in existing:
            devices.append({"id": existing[name], "name": name})
            print(f"  Reusing device: {name}")
            continue
        r = client.post(
            f"{API_URL}/device-registry",
            headers=headers,
            json={
                "name": name,
                "device_type": "rfid_reader",
                "metadata": {"simulated": True, "profile": "inventory"},
            },
        )
        if r.status_code == 201:
            d = r.json()
            devices.append({"id": d["id"], "name": d["name"]})
            print(f"  Created device: {d['name']}")
    return devices


def _send_inventory_read(
    client: httpx.Client,
    tenant_id: str,
    device_id: str,
    item: dict[str, str],
    serial: int,
) -> int:
    epc_hex = _sgtin96_hex(item["company_prefix"], item["item_ref"], serial)
    body: dict[str, object] = {
        "device_id": device_id,
        "tag_id": epc_hex,
        "timestamp": datetime.now(UTC).isoformat(),
        "signal_strength": round(random.uniform(-65.0, -35.0), 1),
        "identity": {"epc_hex": epc_hex},
        "tag_data": {"lot": "LOT-A"},
    }
    r = client.post(
        f"{API_URL}/tag-reads",
        headers=_headers(tenant_id),
        json=body,
    )
    return r.status_code


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TagPulse inventory simulator"
    )
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--devices", type=int, default=2)
    parser.add_argument("--interval", type=float, default=1.5)
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--seed-only", action="store_true")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("TAGPULSE_API_KEY"),
        help="Admin/editor API key (Bearer). Falls back to $TAGPULSE_API_KEY.",
    )
    args = parser.parse_args()

    global _API_KEY
    _API_KEY = args.api_key
    if not _API_KEY:
        print(
            "WARNING: no --api-key (or $TAGPULSE_API_KEY) provided — "
            "product/lot/device writes will fail with 403. "
            "See docs/quickstart.md → Step 5b for how to bootstrap one."
        )

    client = httpx.Client(timeout=10.0)
    try:
        h = client.get(f"{API_URL}/health")
        if h.status_code != 200:
            print(f"API unhealthy: {h.status_code}")
            sys.exit(1)
    except httpx.ConnectError:
        print(f"Cannot reach API at {API_URL}")
        sys.exit(1)

    print("\n=== TagPulse Inventory Simulator ===")
    print(f"Tenant: {args.tenant_id}\n")

    print("Seeding product catalog...")
    catalog = _seed_catalog(client, args.tenant_id)
    if not catalog:
        print("No catalog items — aborting.")
        sys.exit(1)
    print(f"Catalog ready: {len(catalog)} products.\n")

    print("Registering simulated devices...")
    devices = _seed_devices(client, args.tenant_id, args.devices)
    if not devices:
        sys.exit(1)
    print(f"Devices ready: {len(devices)}.\n")

    if args.seed_only:
        return

    print("Streaming SGTIN tag reads (Ctrl+C to stop)...\n")
    serial = random.randrange(1, 1 << 30)
    sent = 0
    failed = 0
    start = time.monotonic()
    try:
        while True:
            for device in devices:
                item = random.choice(catalog)
                serial = (serial + 1) & ((1 << 38) - 1)
                code = _send_inventory_read(
                    client, args.tenant_id, device["id"], item, serial
                )
                if code == 201:
                    sent += 1
                else:
                    failed += 1
                print(
                    f"  {device['name']} {item['sku']}: {sent} sent, "
                    f"{failed} failed",
                    end="\r",
                )
            time.sleep(args.interval * random.uniform(0.7, 1.3))
            if args.duration and (time.monotonic() - start) > args.duration:
                break
    except KeyboardInterrupt:
        pass

    elapsed = time.monotonic() - start
    print(
        f"\n\nDone: {sent} sent, {failed} failed in {elapsed:.0f}s "
        f"({sent / max(elapsed, 1):.1f} reads/sec)"
    )


if __name__ == "__main__":
    main()
